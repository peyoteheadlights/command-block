"""Our own small Discord client: the gateway (one outgoing websocket) and the
REST calls the bot uses, Discord API v10 (ADR 0003).

Everything here is about not getting the owner's home address banned:
Discord blocks an address for a while after 10,000 refused requests in 10
minutes. So a rejected token (401) stops the bot and is never retried, a
channel that answers 403 is left alone for a while, 429 waits as long as
Discord says, and each route waits for its own rate-limit bucket.

The network goes through two swappable seams, TRANSPORT (REST, an httpx
transport) and CONNECT (the gateway), the same way downloads.TRANSPORT is
swapped in tests. The token is never logged and never in an error.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from .. import __version__

log = logging.getLogger("msc.discord")

API = "https://discord.com/api/v10"
GATEWAY_QUERY = "?v=10&encoding=json"
# Tests swap these for the fake Discord.
TRANSPORT: httpx.AsyncBaseTransport | None = None
CONNECT: Callable[[str], Awaitable[Any]] | None = None

# Gateway intents (https://discord.com/developers/docs/events/gateway#gateway-intents).
GUILDS = 1 << 0
GUILD_MESSAGES = 1 << 9
MESSAGE_CONTENT = 1 << 15  # privileged: the switch must be on in the portal
# The application's flags that say a privileged switch is on in the
# developer portal. The _LIMITED ones are for bots under 100 servers.
FLAG_MEMBERS = (1 << 14) | (1 << 15)
FLAG_MESSAGE_CONTENT = (1 << 18) | (1 << 19)

# Close codes after which trying again can't help.
TOKEN_REJECTED_CLOSE = 4004
INTENTS_CLOSES = (4013, 4014)
FATAL_CLOSES = {TOKEN_REJECTED_CLOSE, 4010, 4011, 4012, *INTENTS_CLOSES}
# Close codes after which the session can't be resumed.
FRESH_CLOSES = {4007, 4009}

FORBIDDEN_PAUSE = 600.0  # seconds a channel that answered 403 is left alone
MAX_WAIT = 60.0  # the longest wait between reconnects


class DiscordError(Exception):
    """Discord said no. ``status`` is the HTTP status, ``code`` Discord's own."""

    def __init__(self, message: str, status: int = 0, code: int = 0):
        super().__init__(message)
        self.status = status
        self.code = code


class TokenRejected(DiscordError):
    """The token is wrong or was reset: the bot stops until a new one is set."""

    def __init__(self) -> None:
        super().__init__("Discord refused the bot's token.", 401)


class IntentsRefused(DiscordError):
    """The gateway refused a switch (intent) that isn't on in the portal."""


def _route(method: str, path: str) -> str:
    """The rate-limit route of a request: its path with ids blanked, except
    the ones Discord keeps buckets per (channel, guild, webhook)."""
    parts = path.strip("/").split("/")
    out = []
    for i, part in enumerate(parts):
        major = i > 0 and parts[i - 1] in ("channels", "guilds", "webhooks")
        out.append(part if major or not part.isdigit() else ":id")
    return f"{method} /{'/'.join(out)}"


_CHANNEL = re.compile(r"^/channels/(\d+)")


class Rest:
    """Discord's REST API, with its rate limits kept."""

    def __init__(self, token: str):
        self._http = httpx.AsyncClient(
            base_url=API,
            transport=TRANSPORT,
            timeout=15,
            headers={
                "Authorization": f"Bot {token}",
                "User-Agent": f"DiscordBot (https://github.com/peyoteheadlights/command-block, {__version__})",
            },
        )
        self.rejected = False
        self._bucket_of: dict[str, str] = {}  # route -> Discord's bucket id
        self._open_at: dict[str, float] = {}  # bucket -> when it has room again
        self._global_open_at = 0.0
        self._forbidden: dict[str, float] = {}  # channel id -> until when

    async def close(self) -> None:
        await self._http.aclose()

    async def request(self, method: str, path: str, body: Any = None) -> Any:
        if self.rejected:
            raise TokenRejected()
        channel = _CHANNEL.match(path)
        if channel and self._forbidden.get(channel.group(1), 0) > time.monotonic():
            raise DiscordError("The bot isn't allowed in that channel.", 403, 50013)
        route = _route(method, path)
        for _attempt in range(4):
            bucket = self._bucket_of.get(route, route)
            wait = max(self._global_open_at, self._open_at.get(bucket, 0)) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                answer = await self._http.request(
                    method, path, json=body if body is not None else None
                )
            except httpx.HTTPError as exc:
                # The message names the address, never the headers (the token).
                raise DiscordError(f"Couldn't reach Discord ({type(exc).__name__}).") from None
            self._remember_bucket(route, answer)
            if answer.status_code == 429:
                data = _json(answer)
                retry = float(data.get("retry_after") or answer.headers.get("retry-after") or 1)
                until = time.monotonic() + retry
                if data.get("global"):
                    self._global_open_at = until
                else:
                    self._open_at[self._bucket_of.get(route, route)] = until
                log.info("Discord asked to wait %.1f s (%s)", retry, route)
                continue
            if answer.status_code == 401:
                self.rejected = True
                raise TokenRejected()
            if answer.status_code >= 400:
                data = _json(answer)
                if answer.status_code == 403 and channel:
                    self._forbidden[channel.group(1)] = time.monotonic() + FORBIDDEN_PAUSE
                raise DiscordError(
                    str(data.get("message") or f"Discord answered {answer.status_code}."),
                    answer.status_code,
                    int(data.get("code") or 0),
                )
            return _json(answer) if answer.content else None
        raise DiscordError("Discord kept asking the bot to wait.", 429)

    def _remember_bucket(self, route: str, answer: httpx.Response) -> None:
        bucket = answer.headers.get("x-ratelimit-bucket")
        if not bucket:
            return
        self._bucket_of[route] = bucket
        if answer.headers.get("x-ratelimit-remaining") == "0":
            after = float(answer.headers.get("x-ratelimit-reset-after") or 0)
            self._open_at[bucket] = time.monotonic() + after


def _json(answer: httpx.Response) -> Any:
    try:
        return answer.json()
    except ValueError:
        return {}


async def _connect(url: str) -> Any:
    if CONNECT is not None:
        return await CONNECT(url)
    return await websockets.connect(url, max_size=2**22, open_timeout=20)


class Gateway:
    """The bot's live connection: logs in, keeps the heartbeat, resumes after
    a drop and waits longer after each failure. ``on_event(name, data)`` is
    called for each dispatch (READY, GUILD_CREATE, INTERACTION_CREATE...)."""

    def __init__(
        self,
        rest: Rest,
        token: str,
        intents: int,
        on_event: Callable[[str, dict], Awaitable[None]],
        before_login: Callable[[], Awaitable[None]] | None = None,
    ):
        self.rest = rest
        self._token = token
        self.intents = intents
        self.on_event = on_event
        self.before_login = before_login  # may change the intents
        self.state = (
            "connecting"  # connecting | connected | token_rejected | intents_refused | stopped
        )
        self.detail = ""  # why it isn't connected, in plain words
        self.connected_at: float | None = None
        self.failures = 0
        self._session: str | None = None
        self._resume_url: str | None = None
        self._seq: int | None = None
        self._acked = True
        self._socket: Any = None
        self.presence: str | None = None  # the line under the bot's name

    async def run(self) -> None:
        """Until stopped, the token is rejected or a switch is refused."""
        while True:
            try:
                await self._once()
                self.failures = 0
            except TokenRejected:
                self.rest.rejected = True
                self._down("token_rejected", "Discord refused the token.")
                return
            except IntentsRefused as exc:
                self._down("intents_refused", str(exc))
                return
            except asyncio.CancelledError:
                self._down("stopped", "")
                raise
            except Exception as exc:  # a drop, a timeout, a bad answer: try again
                self.failures += 1
                self._down("connecting", _reason(exc))
                log.info("Discord connection lost: %s", type(exc).__name__)
            # 1, 2, 4 ... seconds up to a minute, with a little jitter, so a
            # flaky night never uses up Discord's daily logins.
            wait = min(MAX_WAIT, 2 ** max(self.failures - 1, 0)) * random.uniform(0.8, 1.2)
            await asyncio.sleep(wait if self.failures else random.uniform(1, 5))

    def _down(self, state: str, detail: str) -> None:
        self.state, self.detail, self.connected_at = state, detail, None

    async def _once(self) -> None:
        if self._session and self._resume_url:
            url = self._resume_url
        else:
            if self.before_login:
                await self.before_login()
            info = await self.rest.request("GET", "/gateway/bot")
            limit = info.get("session_start_limit") or {}
            if limit.get("remaining") == 0:
                wait = float(limit.get("reset_after") or 60_000) / 1000
                raise DiscordError(f"Discord's daily logins are used up; trying in {wait:.0f} s")
            url = info["url"]
        socket = self._socket = await _connect(f"{url}/{GATEWAY_QUERY}")
        beat: asyncio.Task | None = None
        try:
            hello = json.loads(await socket.recv())
            interval = float(hello["d"]["heartbeat_interval"]) / 1000
            self._acked = True
            beat = asyncio.create_task(self._heartbeat(socket, interval), name="discord-heartbeat")
            if self._session:
                await self._send(
                    socket,
                    6,
                    {"token": self._token, "session_id": self._session, "seq": self._seq},
                )
            else:
                await self._identify(socket)
            while True:
                await self._handle(socket, json.loads(await socket.recv()))
        except ConnectionClosed as exc:
            code = exc.rcvd.code if exc.rcvd else None
            if code == TOKEN_REJECTED_CLOSE:
                raise TokenRejected() from None
            if code in INTENTS_CLOSES:
                raise IntentsRefused(
                    "Discord refused a switch the bot asked for. Turn it on in the developer portal."
                ) from None
            if code in FATAL_CLOSES:
                raise DiscordError(f"Discord closed the connection ({code}).") from None
            if code in FRESH_CLOSES:
                self._session = None
            raise
        finally:
            if beat:
                beat.cancel()
            try:
                await socket.close()
            except Exception:  # already closed
                pass

    async def _identify(self, socket: Any) -> None:
        hello: dict[str, Any] = {
            "token": self._token,
            "intents": self.intents,
            "properties": {
                "os": "windows",
                "browser": "command-block",
                "device": "command-block",
            },
        }
        if self.presence:
            hello["presence"] = _presence(self.presence)
        await self._send(socket, 2, hello)

    async def set_presence(self, words: str) -> None:
        """The bot's status line; kept for the next login too."""
        self.presence = words
        if self.state == "connected" and self._socket is not None:
            await self._send(self._socket, 3, _presence(words))

    async def _handle(self, socket: Any, message: dict) -> None:
        op = message.get("op")
        if op == 0:
            self._seq = message.get("s") or self._seq
            name, data = message.get("t") or "", message.get("d") or {}
            if name == "READY":
                self._session = data.get("session_id")
                self._resume_url = data.get("resume_gateway_url")
            if name in ("READY", "RESUMED"):
                self.state, self.detail, self.connected_at = "connected", "", time.time()
            try:
                await self.on_event(name, data)
            except Exception:
                log.exception("the bot couldn't handle %s", name)
        elif op == 1:
            await self._send(socket, 1, self._seq)
        elif op == 7:  # Discord asks for a reconnect: resume
            await socket.close(code=4900)
        elif op == 9:  # invalid session
            if not message.get("d"):
                self._session = None
            await asyncio.sleep(random.uniform(1, 5))
            if self._session:
                await self._send(
                    socket,
                    6,
                    {"token": self._token, "session_id": self._session, "seq": self._seq},
                )
            else:
                await self._identify(socket)
        elif op == 11:
            self._acked = True

    async def _heartbeat(self, socket: Any, interval: float) -> None:
        await asyncio.sleep(interval * random.random())
        while True:
            if not self._acked:
                # No answer to the last beat: the connection is dead even if
                # the socket looks open. Close it and resume.
                log.info("Discord stopped answering the heartbeat; reconnecting")
                await socket.close(code=4901)
                return
            self._acked = False
            await self._send(socket, 1, self._seq)
            await asyncio.sleep(interval)

    @staticmethod
    async def _send(socket: Any, op: int, data: Any) -> None:
        await socket.send(json.dumps({"op": op, "d": data}))


def _presence(words: str) -> dict[str, Any]:
    # A custom status (type 4): the words alone, no "Playing" in front.
    return {
        "since": None,
        "activities": [{"name": "Custom Status", "type": 4, "state": words[:128]}],
        "status": "online",
        "afk": False,
    }


def _reason(exc: BaseException) -> str:
    if isinstance(exc, ConnectionClosed):
        return "The connection to Discord dropped."
    if isinstance(exc, DiscordError):
        return str(exc)
    return "Can't reach Discord. Is this PC online?"
