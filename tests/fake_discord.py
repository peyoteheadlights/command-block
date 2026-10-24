"""A fake Discord for the bot's tests, like the fake Minecraft server.

It plugs into the Discord client's two seams: REST calls go to ``handle``
(an httpx.MockTransport), the gateway to ``connect``. It keeps one Discord
server's roles and channels, records every REST call, answers 401 for a
wrong token, and lets a test push gateway events (a slash command, a button
press, a message) or drop the connection. No test touches the network.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import threading
import time

import httpx
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

GOOD_TOKEN = "MTAwMDAwMDAwMDAwMDAwMDAw.GfAkEe.abcdefghijklmnopqrstuvwxyz0123456789ABCD"
APP_ID = "1000000000000000000"
BOT_ID = "1000000000000000001"
GUILD_ID = "2000000000000000000"
BOT_ROLE = "2000000000000000001"
MEMBER_ID = "3000000000000000000"
FLAGS_BOTH = (1 << 15) | (1 << 19)  # both switches on (the _LIMITED flags)


class FakeSocket:
    def __init__(self, discord: FakeDiscord, url: str):
        self.discord = discord
        self.url = url
        self.loop = asyncio.get_running_loop()
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False
        self.inbox.put_nowait({"op": 10, "d": {"heartbeat_interval": discord.heartbeat_ms}})

    async def send(self, text: str) -> None:
        message = json.loads(text)
        self.sent.append(message)
        self.discord.gateway_sent.append(message)
        op = message["op"]
        if op == 1:
            if self.discord.ack_heartbeats:
                self.inbox.put_nowait({"op": 11})
        elif op == 2:
            if message["d"]["token"] != self.discord.token:
                self.drop(4004)
                return
            self.discord.identifies += 1
            self.dispatch(
                "READY",
                {
                    "v": 10,
                    "user": {"id": BOT_ID, "username": self.discord.bot_name, "bot": True},
                    "session_id": f"session-{self.discord.identifies}",
                    "resume_gateway_url": "wss://resume.fake",
                    "application": {"id": APP_ID, "flags": self.discord.flags},
                    "guilds": [{"id": GUILD_ID, "unavailable": True}],
                },
            )
            self.dispatch("GUILD_CREATE", {"id": GUILD_ID, "name": self.discord.guild_name})
        elif op == 6:
            self.discord.resumes += 1
            self.dispatch("RESUMED", {})

    def dispatch(self, name: str, data: dict) -> None:
        self.discord.seq += 1
        item = {"op": 0, "t": name, "s": self.discord.seq, "d": data}
        self.loop.call_soon_threadsafe(self.inbox.put_nowait, item)

    def drop(self, code: int = 1006) -> None:
        self.loop.call_soon_threadsafe(self.inbox.put_nowait, Close(code, "fake"))

    async def recv(self) -> str:
        item = await self.inbox.get()
        if isinstance(item, Close):
            self.closed = True
            raise ConnectionClosed(item, None)
        return json.dumps(item)

    async def close(self, code: int = 1000) -> None:
        if not self.closed:
            self.closed = True
            self.inbox.put_nowait(Close(code, "closed"))


class FakeDiscord:
    def __init__(self) -> None:
        self.token = GOOD_TOKEN
        self.flags = FLAGS_BOTH
        self.bot_name = "My bot"
        self.guild_name = "Friends"
        self.heartbeat_ms = 41250
        self.ack_heartbeats = True
        self.identifies = 0
        self.resumes = 0
        self.seq = 0
        self.calls: list[tuple[str, str, object]] = []
        self.gateway_sent: list[dict] = []
        self.sockets: list[FakeSocket] = []
        self.forbidden_channels: set[str] = set()
        self.rate_limit_next: float | None = None
        self.lock = threading.Lock()
        self._ids = itertools.count(4000000000000000000)
        self.roles: list[dict] = [
            {
                "id": GUILD_ID,
                "name": "@everyone",
                "position": 0,
                "permissions": str(1 << 10 | 1 << 11 | 1 << 6 | 1 << 16),
            },
            {
                "id": BOT_ROLE,
                "name": "My bot",
                "position": 5,
                "managed": True,
                "permissions": str(1 << 28 | 1 << 4),
            },
            {
                "id": "2000000000000000002",
                "name": "Friends of Sam",
                "position": 2,
                "permissions": "0",
            },
        ]
        self.channels: list[dict] = [
            {"id": "2000000000000000010", "type": 0, "name": "general", "permission_overwrites": []}
        ]
        self.bot_roles = [BOT_ROLE]
        self.messages: list[dict] = []

    # -- seams -----------------------------------------------------------------
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    async def connect(self, url: str) -> FakeSocket:
        socket = FakeSocket(self, url)
        self.sockets.append(socket)
        return socket

    @property
    def socket(self) -> FakeSocket:
        return self.sockets[-1]

    # -- test helpers ----------------------------------------------------------------
    def command(
        self,
        name: str,
        options: dict | None = None,
        roles=(),
        channel: str | None = None,
        member: str = MEMBER_ID,
        username: str = "sam",
    ) -> str:
        """A member runs a slash command; returns the interaction's token."""
        token = f"itoken{next(self._ids)}"
        self.socket.dispatch(
            "INTERACTION_CREATE",
            {
                "id": str(next(self._ids)),
                "type": 2,
                "token": token,
                "guild_id": GUILD_ID,
                "channel_id": channel or "2000000000000000010",
                "member": {"user": {"id": member, "username": username}, "roles": list(roles)},
                "data": {
                    "name": name,
                    "options": [
                        {"name": k, "value": v, "type": 3} for k, v in (options or {}).items()
                    ],
                },
            },
        )
        return token

    def press(
        self,
        custom_id: str,
        roles=(),
        member: str = MEMBER_ID,
        username: str = "sam",
        channel: str | None = None,
    ) -> str:
        """A member presses a button; returns the interaction's token."""
        token = f"itoken{next(self._ids)}"
        self.socket.dispatch(
            "INTERACTION_CREATE",
            {
                "id": str(next(self._ids)),
                "type": 3,
                "token": token,
                "guild_id": GUILD_ID,
                "channel_id": channel or "2000000000000000010",
                "member": {"user": {"id": member, "username": username}, "roles": list(roles)},
                "data": {"custom_id": custom_id, "component_type": 2},
            },
        )
        return token

    def message(
        self,
        content: str,
        channel: str,
        member: str = MEMBER_ID,
        username: str = "sam",
        roles=(),
        nick: str | None = None,
        **extra,
    ) -> str:
        """Someone writes in a channel; returns the message's id."""
        made = str(next(self._ids))
        self.socket.dispatch(
            "MESSAGE_CREATE",
            {
                "id": made,
                "type": 0,
                "guild_id": GUILD_ID,
                "channel_id": channel,
                "content": content,
                "author": {"id": member, "username": username},
                "member": {"roles": list(roles), "nick": nick},
                "mentions": [],
                **extra,
            },
        )
        return made

    def channel_id(self, name: str) -> str:
        return next(c["id"] for c in self.channels if c["name"] == name)

    def answers(self, token: str) -> list[dict]:
        """Everything the bot sent for one interaction: callbacks, then edits."""
        return [body for _m, path, body in self.calls if token in path and isinstance(body, dict)]

    def in_channel(self, name: str) -> list[dict]:
        """The messages the bot posted in a channel, as they are now."""
        found = self.channel_id(name)
        return [m for m in self.messages if m["channel_id"] == found]

    def replies(self, token: str) -> list[str]:
        """What the bot answered to one interaction, first reply then edits."""
        out = []
        for _method, path, body in self.calls:
            if token in path and isinstance(body, dict):
                data = body.get("data", body)
                if "content" in data:
                    out.append(data["content"])
        return out

    def made(self, method: str, prefix: str) -> list:
        return [body for m, p, body in self.calls if m == method and p.startswith(prefix)]

    # -- REST ----------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v10")
        body = json.loads(request.content) if request.content else None
        with self.lock:
            self.calls.append((request.method, path, body))
        if request.headers.get("authorization") != f"Bot {self.token}":
            return httpx.Response(401, json={"message": "401: Unauthorized", "code": 0})
        if self.rate_limit_next is not None:
            retry, self.rate_limit_next = self.rate_limit_next, None
            return httpx.Response(
                429, json={"message": "slow", "retry_after": retry, "global": False}
            )
        key = (request.method, path)
        if path.startswith("/channels/") and path.split("/")[2] in self.forbidden_channels:
            return httpx.Response(403, json={"message": "Missing Access", "code": 50001})
        if key == ("GET", "/gateway/bot"):
            return httpx.Response(
                200,
                json={
                    "url": "wss://gateway.fake",
                    "session_start_limit": {"remaining": 990, "reset_after": 1000},
                },
            )
        if key == ("GET", "/applications/@me"):
            return httpx.Response(200, json={"id": APP_ID, "name": "My bot", "flags": self.flags})
        if key == ("PATCH", "/users/@me"):
            self.bot_name = body.get("username") or self.bot_name
            return httpx.Response(200, json={"id": BOT_ID, "username": self.bot_name, "bot": True})
        if request.method == "PUT" and path.endswith("/commands"):
            return httpx.Response(200, json=body)
        if path.startswith("/interactions/") or path.startswith("/webhooks/"):
            return httpx.Response(204 if path.startswith("/interactions/") else 200, json={})
        if key == ("GET", f"/guilds/{GUILD_ID}/roles"):
            return httpx.Response(200, json=self.roles)
        if key == ("GET", f"/guilds/{GUILD_ID}/channels"):
            return httpx.Response(200, json=self.channels)
        if key == ("GET", f"/guilds/{GUILD_ID}/members/{BOT_ID}"):
            return httpx.Response(200, json={"user": {"id": BOT_ID}, "roles": self.bot_roles})
        if key == ("POST", f"/guilds/{GUILD_ID}/channels"):
            channel = {"id": str(next(self._ids)), "permission_overwrites": [], **body}
            channel["name"] = (
                channel["name"].lower().replace(" ", "-")
                if channel["type"] != 4
                else channel["name"]
            )
            self.channels.append(channel)
            return httpx.Response(201, json=channel)
        if key == ("POST", f"/guilds/{GUILD_ID}/roles"):
            role = {"id": str(next(self._ids)), "position": 1, "managed": False, **body}
            self.roles.append(role)
            return httpx.Response(200, json=role)
        if (
            request.method == "POST"
            and path.startswith("/channels/")
            and path.endswith("/messages")
        ):
            message = {"id": str(next(self._ids)), "channel_id": path.split("/")[2], **body}
            self.messages.append(message)
            return httpx.Response(200, json=message)
        if request.method == "PUT" and "/reactions/" in path:
            return httpx.Response(204)
        if request.method == "PATCH" and path.startswith("/channels/") and "/messages/" in path:
            message = next((m for m in self.messages if m["id"] == path.split("/")[-1]), None)
            if message is None:
                return httpx.Response(404, json={"message": "Unknown Message", "code": 10008})
            message.update(body)
            return httpx.Response(200, json=message)
        return httpx.Response(404, json={"message": "Unknown", "code": 10000})


def wait_for(check, timeout: float = 10.0):
    """Waits until ``check()`` is truthy (the bot runs in the agent's loop)."""
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.02)
