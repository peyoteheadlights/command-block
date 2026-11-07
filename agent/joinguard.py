"""Join protection (Technical help: the join flood guard), for Java servers.

In plain words: with Join protection on, the server is started on
127.0.0.1 with a private port (``--port``), and the guard takes the
server's own port. Every player connects to the guard first. It reads only
the first packets (the handshake, a server list ping or Login Start, never
anything after) and then passes the bytes straight through.

Before passing a player on, it checks:

  * limits per address (IPv6 counted per /64): open connections, pings a
    second, one login every 4 seconds, 5 seconds to say hello. 5 limit
    breaks in 10 minutes block the address for 1 hour, doubling to 24
    hours. Blocks the owner adds stay until removed.
  * a burst of names it has never seen: "known players only" for 5
    minutes, or in a server's first 7 days and in Launch mode, Slow mode,
    where new names get in at a set rate through the Join queue.
  * optionally, only addresses from the home network and Tailscale.

The real player address reaches the server by PROXY protocol v2, only once
the type's add-on and its setting are seen on disk (``proxy_addon``).
Without it the server sees 127.0.0.1 for everyone, so ``prevent-proxy-
connections`` is turned off for the run and "ban-ip" goes to the guard.

It fails open: a check that throws lets that player through, a guard that
stops is started again within seconds, a port it can't take means the
server starts direct. Each sends an alert. The same listener answers for a
slept server when "Start when someone joins" is on.

``Rules`` is the plain logic with its own clock, so the tests drive it
without sockets. ``Listener`` is one server's port, ``GuardHub`` owns the
guard's thread and talks to the rest of the agent.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import struct
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .events import Event
from .minecraft.properties import FILENAME, PropertiesFile

if TYPE_CHECKING:
    from .core import AgentCore, ServerContext

log = logging.getLogger("msc.joinguard")

# The two sizes of starting limits (Mark, 2026-10-30): open connections per
# address, never-seen names a minute that count as a burst, pings a second
# per address, and how many new names a minute Slow mode lets in.
PRESETS: dict[str, dict[str, int]] = {
    "small": {"connections": 3, "new_names": 15, "pings": 10, "slow_rate": 10},
    "big": {"connections": 6, "new_names": 60, "pings": 30, "slow_rate": 60},
}
LIMIT_KEYS = tuple(PRESETS["small"])
LOGIN_EVERY = 4.0
HANDSHAKE_SECONDS = 5.0
BURST_WINDOW = 60.0
BURST_FOR = 300.0
NEW_SERVER_DAYS = 7
BREAKS_TO_BLOCK = 5
BREAK_WINDOW = 600.0
FIRST_BLOCK = 3600.0
LONGEST_BLOCK = 86400.0
# Under the game's 30-second read timeout (phase11b/join-protection-research.md).
HOLD_SECONDS = 25.0
PLACE_KEPT = 120.0
BIG_AT = 20  # players online at once, measured within 30 days
PEAK_DAYS = 30
LAUNCH_HOURS = (1, 168)
SUPERVISE_SECONDS = 2.0
KNOWN_SECONDS = 30.0
CACHE_SECONDS = 1.0
MAX_PACKET = 8192
LOG_KEPT = 500
FAIL_ALERT_EVERY = 600.0
# This PC itself (the playit agent, the app's own checks). Not limited per
# address, because everyone coming that way shares it; bursts still count.
THIS_PC = frozenset({"127.0.0.1", "::1"})
HOME_NETS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "127.0.0.0/8",
        "100.64.0.0/10",  # Tailscale
        "fd7a:115c:a1e0::/48",  # Tailscale
        "fc00::/7",
        "fe80::/10",
        "::1/128",
    )
)
PROXY_V2 = b"\r\n\r\n\x00\r\nQUIT\n"
# Tests run the fake server from a start command; real start commands
# might not pass --port on, so the guard leaves them alone.
RAW_COMMAND_OK = False
# What players see in Minecraft (in-game text is English, like auto-sleep's).
WORDS = {
    "join.too_fast": "You tried to join too quickly. Wait a few seconds and try again.",
    "join.known_only": (
        "Lots of new names are joining right now, so only players this server "
        "knows can join. Try again in a few minutes."
    ),
    "join.in_line": "You're #{place} in line. Join again in about a minute to keep your place.",
    "join.starting": "The server is starting. Try again in about a minute.",
    "join.asleep": "Asleep, join to start it",
}


class Bad(Exception):
    """The first bytes aren't Minecraft's."""


# ---------------------------------------------------------------- addresses
def plain(host: str) -> str:
    ip = ipaddress.ip_address(host.split("%")[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return str(ip)


def address_key(host: str) -> str:
    """What the limits count: the address, or its /64 for IPv6, since one
    home gets a whole /64 and could change address at will."""
    text = host.strip()
    if "/" in text:
        return str(ipaddress.ip_network(text, strict=False))
    ip = ipaddress.ip_address(plain(text))
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def at_home(host: str) -> bool:
    ip = ipaddress.ip_address(plain(host))
    return any(ip in net for net in HOME_NETS if net.version == ip.version)


# ---------------------------------------------------------------- the rules
class Rules:
    """One server's limits, blocks, burst rule and Join queue."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.limits = dict(PRESETS["small"])
        self.slow = False  # a burst slows new names instead of refusing them
        self.launch = False  # Launch mode: Slow mode the whole time
        self.home_only = False
        self.known: Callable[[str], bool] = lambda name: False
        self.open: dict[str, int] = {}
        self.pings: dict[str, deque[float]] = {}
        self.last_login: dict[str, float] = {}
        self.breaks: dict[str, deque[float]] = {}
        # key -> {"until": float | None, "times": int, "why": str, "by": str}
        self.blocks: dict[str, dict[str, Any]] = {}
        self.new_names: dict[str, float] = {}
        self.burst_until = 0.0
        self.tokens = 0.0
        self.tokens_at = 0.0
        self.queue: list[dict[str, Any]] = []
        self.turned_away: dict[str, int] = {}  # day -> count
        self.on_block: Callable[[str, dict[str, Any] | None], None] = lambda key, block: None

    # -- per address ------------------------------------------------------
    def refused(self, key: str, host: str) -> str | None:
        """Why this address may not connect at all, or None."""
        block = self.blocks.get(key)
        if block and block["until"] is not None and block["until"] <= self.clock():
            block["until"] = 0.0  # ran out; "times" is kept so the next one is longer
        if block and block["until"] != 0.0:
            return "blocked"
        if self.home_only and not at_home(host):
            return "home_only"
        return None

    def connect(self, key: str) -> bool:
        if self.open.get(key, 0) >= self.limits["connections"]:
            return False
        self.open[key] = self.open.get(key, 0) + 1
        return True

    def disconnect(self, key: str) -> None:
        left = self.open.get(key, 0) - 1
        if left > 0:
            self.open[key] = left
        else:
            self.open.pop(key, None)

    def ping(self, key: str) -> bool:
        now = self.clock()
        seen = self.pings.setdefault(key, deque())
        while seen and now - seen[0] >= 1.0:
            seen.popleft()
        if len(seen) >= self.limits["pings"]:
            return False
        seen.append(now)
        return True

    def broke(self, key: str, why: str) -> bool:
        """Count one limit break. True when it blocked the address."""
        now = self.clock()
        self.count_away()
        seen = self.breaks.setdefault(key, deque())
        while seen and now - seen[0] >= BREAK_WINDOW:
            seen.popleft()
        seen.append(now)
        if len(seen) < BREAKS_TO_BLOCK:
            return False
        seen.clear()
        times = (self.blocks.get(key) or {}).get("times", 0) + 1
        length = min(FIRST_BLOCK * 2 ** (times - 1), LONGEST_BLOCK)
        self.block(key, now + length, why, "guard", times)
        return True

    def block(
        self, key: str, until: float | None, why: str, by: str, times: int | None = None
    ) -> None:
        old = self.blocks.get(key) or {}
        self.blocks[key] = {
            "until": until,
            "times": times if times is not None else old.get("times", 0),
            "why": why,
            "by": by,
            "since": self.clock(),
        }
        self.on_block(key, self.blocks[key])

    def unblock(self, key: str) -> bool:
        if self.blocks.pop(key, None) is None:
            return False
        self.on_block(key, None)
        return True

    def block_list(self) -> list[dict[str, Any]]:
        now = self.clock()
        return [
            {"address": key, **block}
            for key, block in sorted(self.blocks.items())
            if block["until"] is None or block["until"] > now
        ]

    def count_away(self) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime(self.clock()))
        if day not in self.turned_away:
            self.turned_away = {day: 0}
        self.turned_away[day] += 1

    def away_today(self) -> int:
        return self.turned_away.get(time.strftime("%Y-%m-%d", time.localtime(self.clock())), 0)

    # -- logins -----------------------------------------------------------
    @property
    def burst(self) -> bool:
        return self.clock() < self.burst_until

    def login(self, name: str, key: str, this_pc: bool) -> tuple[str, str | None]:
        """("in", None), ("wait", None) or ("refuse", words key)."""
        now = self.clock()
        if not this_pc:
            last = self.last_login.get(key)
            self.last_login[key] = now
            if last is not None and now - last < LOGIN_EVERY:
                self.broke(key, "logins")
                return "refuse", "join.too_fast"
        if self.known(name):
            return "in", None
        lowered = name.lower()
        self.new_names = {n: t for n, t in self.new_names.items() if now - t < BURST_WINDOW}
        self.new_names.setdefault(lowered, now)
        if len(self.new_names) >= self.limits["new_names"]:
            self.burst_until = now + BURST_FOR  # starts the 5 minutes again while it lasts
        if self.launch or (self.burst and self.slow):
            return ("in", None) if self.turn(name, key) else ("wait", None)
        if self.burst:
            self.count_away()
            return "refuse", "join.known_only"
        return "in", None

    # -- the Join queue -----------------------------------------------------
    def _refill(self, now: float) -> None:
        rate = self.limits["slow_rate"] / 60.0
        most = max(1.0, self.limits["slow_rate"] / 6.0)
        if not self.tokens_at:
            self.tokens, self.tokens_at = most, now
        self.tokens = min(most, self.tokens + (now - self.tokens_at) * rate)
        self.tokens_at = now

    def _entry(self, name: str, key: str) -> dict[str, Any] | None:
        lowered = name.lower()
        return next((e for e in self.queue if e["name"] == lowered and e["key"] == key), None)

    def turn(self, name: str, key: str) -> bool:
        """Whether this waiting player may go in now. Their place is kept by
        name and address; places of players who left run out after 2 minutes."""
        now = self.clock()
        self.queue = [e for e in self.queue if e["waiting"] or now - e["seen"] < PLACE_KEPT]
        entry = self._entry(name, key)
        if entry is None:
            entry = {"name": name.lower(), "key": key, "since": now, "seen": now, "waiting": True}
            self.queue.append(entry)
        entry["waiting"], entry["seen"] = True, now
        self._refill(now)
        first = next(e for e in self.queue if e["waiting"])
        if first is not entry or self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        self.queue.remove(entry)
        return True

    def leave(self, name: str, key: str) -> None:
        entry = self._entry(name, key)
        if entry is not None:
            entry["waiting"], entry["seen"] = False, self.clock()

    def place(self, name: str, key: str) -> int:
        entry = self._entry(name, key)
        return self.queue.index(entry) + 1 if entry else len(self.queue) + 1


# ---------------------------------------------------------------- packets
def varint(value: int) -> bytes:
    out = bytearray()
    value &= 0xFFFFFFFF
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 35, 7):
        if pos >= len(data):
            raise Bad("cut short")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
    raise Bad("varint too long")


def read_string(data: bytes, pos: int, most: int) -> tuple[str, int]:
    length, pos = read_varint(data, pos)
    if length > most * 4 or pos + length > len(data):
        raise Bad("string too long")
    return data[pos : pos + length].decode("utf-8", errors="replace"), pos + length


def login_name(raw: bytes) -> str:
    """The name in a Login Start packet (its raw bytes, length first)."""
    _, pos = read_varint(raw, 0)
    packet_id, pos = read_varint(raw, pos)
    if packet_id != 0:
        raise Bad("not Login Start")
    name, _ = read_string(raw, pos, 16)
    if not name or len(name) > 16:
        raise Bad("bad name")
    return name


def packet(packet_id: int, body: bytes) -> bytes:
    inner = varint(packet_id) + body
    return varint(len(inner)) + inner


def mc_string(text: str) -> bytes:
    data = text.encode("utf-8")
    return varint(len(data)) + data


def disconnect(words: str) -> bytes:
    """Login Disconnect: the message the player sees instead of the game."""
    return packet(0, mc_string(json.dumps({"text": words})))


class Feed:
    """Reads Minecraft packets off a stream, keeping every byte read so they
    can be passed on to the server exactly as they came."""

    def __init__(self, reader: asyncio.StreamReader):
        self.reader = reader
        self.kept = bytearray()

    async def exactly(self, count: int) -> bytes:
        data = await self.reader.readexactly(count)
        self.kept += data
        return data

    async def packet(self) -> tuple[int, bytes]:
        length = 0
        for shift in range(0, 21, 7):
            byte = (await self.exactly(1))[0]
            length |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
        else:
            raise Bad("packet length too long")
        if not 0 < length <= MAX_PACKET:
            raise Bad("packet too big")
        body = await self.exactly(length)
        packet_id, pos = read_varint(body, 0)
        return packet_id, body[pos:]


def proxy_header(src: tuple[str, int], dst: tuple[str, int]) -> bytes:
    """A PROXY protocol v2 header saying the connection comes from ``src``."""
    a, b = ipaddress.ip_address(plain(src[0])), ipaddress.ip_address(plain(dst[0]))
    if a.version != b.version:
        b = ipaddress.ip_address("127.0.0.1" if a.version == 4 else "::1")
    family = 0x11 if a.version == 4 else 0x21
    body = a.packed + b.packed + struct.pack("!HH", src[1], dst[1])
    return PROXY_V2 + bytes([0x21, family]) + struct.pack("!H", len(body)) + body


async def read_proxy(reader: asyncio.StreamReader, start: bytes) -> tuple[str, int] | None:
    """The address in a PROXY header (v1 or v2) someone on this PC sent, as
    playit does. None for a header without one (a health check)."""
    if start == PROXY_V2[:2]:
        rest = await reader.readexactly(14)
        if start + rest[:10] != PROXY_V2:
            raise Bad("not a PROXY header")
        family, length = rest[11], struct.unpack("!H", rest[12:14])[0]
        if length > 512:
            raise Bad("PROXY header too long")
        body = await reader.readexactly(length)
        if family == 0x11 and length >= 12:
            return str(ipaddress.ip_address(body[0:4])), struct.unpack("!H", body[8:10])[0]
        if family == 0x21 and length >= 36:
            return str(ipaddress.ip_address(body[0:16])), struct.unpack("!H", body[32:34])[0]
        return None
    line = start + await reader.readuntil(b"\r\n")
    if len(line) > 107 or not line.startswith(b"PROXY "):
        raise Bad("not a PROXY header")
    parts = line.decode("ascii", errors="replace").split()
    if len(parts) >= 6 and parts[1] in ("TCP4", "TCP6"):
        return plain(parts[2]), int(parts[4])
    return None


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


# ---------------------------------------------------------------- one port
class Listener:
    """One server's public port, while the guard holds it."""

    def __init__(self, hub: GuardHub, server_id: str, port: int, host: str = ""):
        self.hub = hub
        self.server_id = server_id
        self.port = port
        self.host = host
        self.mode = "proxy"  # or "asleep": the server is slept, a login starts it
        self.private: int | None = None
        self.header = False  # the server reads PROXY headers (its add-on is on)
        self.rules = Rules()
        self.server: asyncio.Server | None = None
        self.sockets: list[socket.socket] = []
        self.status_cache: tuple[bytes, float] | None = None
        self.playit_seen = 0.0  # when this PC last sent a PROXY header with an address
        self.log: deque[dict[str, Any]] = deque(maxlen=LOG_KEPT)
        self.unsaved: list[dict[str, Any]] = []
        self.asleep_words = ""
        self.version = ""
        self.max_players = 20

    # -- the socket --------------------------------------------------------
    def bind(self) -> None:
        """Take the port. Raises OSError when something else holds it."""
        found: list[socket.socket] = []
        hosts = [self.host] if self.host else ["::", "0.0.0.0"]
        for host in hosts:
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            try:
                sock = socket.socket(family, socket.SOCK_STREAM)
            except OSError:
                continue
            try:
                exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)  # Windows
                if exclusive is not None:
                    sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
                else:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if family == socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                sock.bind((host, self.port))
                sock.listen(128)
                sock.setblocking(False)
            except OSError:
                sock.close()
                if family == socket.AF_INET6 and not self.host:
                    continue  # no IPv6 here: IPv4 alone
                for other in found:
                    other.close()
                raise
            found.append(sock)
            if family == socket.AF_INET6:
                break  # one dual-stack socket covers IPv4 too
        if not found:
            raise OSError(f"port {self.port} couldn't be opened")
        self.sockets = found

    async def serve(self) -> None:
        servers = [await asyncio.start_server(self.handle, sock=sock) for sock in self.sockets]
        self.server = servers[0]
        self._others: list[asyncio.Server] = servers[1:]

    def close(self) -> None:
        servers = [x for x in [self.server, *getattr(self, "_others", [])] if x is not None]
        for server in servers:
            server.close()  # closes its socket too
        if not servers:
            for sock in self.sockets:
                sock.close()
        self.server, self._others = None, []

    # -- one connection ------------------------------------------------------
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or ("0.0.0.0", 0)
        src: tuple[str, int] = (plain(peer[0]), int(peer[1]))
        key, counted = "", ""
        try:
            feed = Feed(reader)
            this_pc = src[0] in THIS_PC
            key = address_key(src[0])
            if not this_pc:
                if not self._let_in(key, src[0]):
                    return
                counted = key
            # One 5-second limit for everything up to Login Start.
            deadline = time.monotonic() + HANDSHAKE_SECONDS

            def left() -> float:
                return max(0.01, deadline - time.monotonic())

            try:
                first = await asyncio.wait_for(reader.readexactly(2), left())
                if this_pc and first in (PROXY_V2[:2], b"PR"):
                    told = await asyncio.wait_for(read_proxy(reader, first), left())
                    if told:
                        src, self.playit_seen = told, time.time()
                    first = await asyncio.wait_for(reader.readexactly(2), left())
            except TimeoutError:
                if not this_pc:
                    self.rules.broke(key, "handshake")
                return
            except (asyncio.IncompleteReadError, Bad, ValueError):
                return
            if this_pc and src[0] not in THIS_PC:  # playit's real address
                this_pc, key = False, address_key(src[0])
                if not self._let_in(key, src[0]):
                    return
                counted = key
            feed.kept += first
            if first[0] == 0xFE:  # an old client's server list ping
                if not this_pc and not self.rules.ping(key):
                    self.rules.broke(key, "pings")
                elif self.mode == "proxy":
                    await self.pipe(reader, writer, bytes(feed.kept), src)
                return
            feed.reader = _Prefixed(reader, first)  # type: ignore[assignment]
            feed.kept.clear()
            try:
                protocol, intent, hello, second = await asyncio.wait_for(
                    self.read_hello(feed), left()
                )
            except TimeoutError:
                if not this_pc:
                    self.rules.broke(key, "handshake")
                return
            except (asyncio.IncompleteReadError, Bad):
                if not this_pc:
                    self.rules.broke(key, "bad_packet")
                return
            try:
                go = await self.decide(
                    (protocol, intent), hello, second, key, this_pc, reader, writer
                )
            except (ConnectionError, OSError, asyncio.IncompleteReadError):
                return
            except Bad:
                if not this_pc:
                    self.rules.broke(key, "bad_packet")
                return
            except Exception:
                log.exception("a Join protection check failed; the player was let through")
                self.hub.failed_open(self.server_id, "check")
                go = self.mode == "proxy"
            if go:
                await self.pipe(reader, writer, hello + second, src)
        except (ConnectionError, OSError):
            pass
        finally:
            if counted:
                self.rules.disconnect(counted)
            writer.close()

    def _let_in(self, key: str, host: str) -> bool:
        """The checks on an address before anything is read: blocks,
        home-only and open connections. A check that throws lets it in."""
        try:
            if self.rules.refused(key, host):
                self.rules.count_away()
                return False
            if not self.rules.connect(key):
                self.rules.broke(key, "connections")
                return False
        except Exception:
            log.exception("a Join protection check failed; the player was let through")
            self.hub.failed_open(self.server_id, "check")
        return True

    async def read_hello(self, feed: Feed) -> tuple[int, int, bytes, bytes]:
        """The handshake's protocol and intent, and its bytes and the next
        packet's."""
        packet_id, body = await feed.packet()
        if packet_id != 0:
            raise Bad("not a handshake")
        protocol, pos = read_varint(body, 0)
        _, pos = read_string(body, pos, 255)
        intent, _ = read_varint(body, pos + 2)
        hello = bytes(feed.kept)
        feed.kept.clear()
        await feed.packet()
        return protocol, intent, hello, bytes(feed.kept)

    async def decide(
        self,
        handshake: tuple[int, int],
        hello: bytes,
        second: bytes,
        key: str,
        this_pc: bool,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> bool:
        """What to do with this player. True: pass them on to the server."""
        rules = self.rules
        protocol, intent = handshake
        if intent == 1:
            if not this_pc and not rules.ping(key):
                rules.broke(key, "pings")
                return False
            if self.mode == "asleep":
                await self.answer(reader, writer, self.asleep_status(protocol))
                return False
            cached = self.status_cache
            if cached and (rules.burst or time.time() - cached[1] < CACHE_SECONDS):
                await self.answer(reader, writer, cached[0])
                return False
            fresh = await self.ask_server(hello + second)
            if fresh is None:
                return False
            self.status_cache = (fresh, time.time())
            await self.answer(reader, writer, fresh)
            return False
        if intent not in (2, 3):
            raise Bad("unknown intent")
        name = login_name(second)
        if self.mode == "asleep":
            verdict, words = ("in", None) if this_pc else rules.login(name, key, False)
            if verdict != "refuse":
                self.hub.wake(self.server_id)
                words = "join.starting"
            self.remember(name, key, "asleep")
            writer.write(disconnect(self.hub.words(words or "join.starting")))
            await writer.drain()
            return False
        verdict, words = rules.login(name, key, this_pc)
        if verdict == "wait":
            verdict = await self.hold(name, key, reader)
            if verdict == "gone":
                return False
            if verdict == "line":
                place = rules.place(name, key)
                self.remember(name, key, "in_line")
                writer.write(disconnect(self.hub.words("join.in_line", place=place)))
                await writer.drain()
                return False
        if verdict == "refuse":
            words = words or "join.known_only"
            self.remember(name, key, words.split(".", 1)[1])
            writer.write(disconnect(self.hub.words(words)))
            await writer.drain()
            return False
        self.remember(name, key, "in")
        return True

    async def hold(self, name: str, key: str, reader: asyncio.StreamReader) -> str:
        """Keep the player on "Logging in..." until it's their turn, at most
        25 seconds. "in", "line" (send their place) or "gone"."""
        deadline = time.time() + HOLD_SECONDS
        while time.time() < deadline:
            if reader.at_eof():
                self.rules.leave(name, key)
                return "gone"
            if self.rules.turn(name, key):
                return "in"
            await asyncio.sleep(0.5)
        self.rules.leave(name, key)
        return "line"

    async def ask_server(self, data: bytes) -> bytes | None:
        """The server's own answer to a server list ping, or None."""
        if not self.private:
            return None
        try:
            up_r, up_w = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self.private), 2
            )
        except (TimeoutError, OSError):
            return None
        try:
            if self.header:
                up_w.write(proxy_header(("127.0.0.1", 0), ("127.0.0.1", self.private)))
            up_w.write(data)
            await up_w.drain()
            feed = Feed(up_r)
            packet_id, _ = await asyncio.wait_for(feed.packet(), 3)
            return bytes(feed.kept) if packet_id == 0 else None
        except (TimeoutError, OSError, asyncio.IncompleteReadError, Bad):
            return None
        finally:
            up_w.close()

    async def answer(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, status: bytes
    ) -> None:
        """Answer a server list ping without the server: the status, then
        the ping's own bytes back (Minecraft's pong is the same packet)."""
        writer.write(status)
        await writer.drain()
        try:
            feed = Feed(reader)
            packet_id, _ = await asyncio.wait_for(feed.packet(), HANDSHAKE_SECONDS)
            if packet_id == 1:
                writer.write(bytes(feed.kept))
                await writer.drain()
        except (TimeoutError, asyncio.IncompleteReadError, Bad):
            pass

    def asleep_status(self, protocol: int) -> bytes:
        status = {
            "version": {"name": self.version, "protocol": protocol},
            "players": {"max": self.max_players, "online": 0},
            "description": {"text": self.asleep_words},
        }
        return packet(0, mc_string(json.dumps(status)))

    async def pipe(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        data: bytes,
        src: tuple[str, int],
    ) -> None:
        if not self.private:
            return
        try:
            up_r, up_w = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self.private), 5
            )
        except (TimeoutError, OSError):
            return
        if self.header:
            up_w.write(proxy_header(src, ("127.0.0.1", self.private)))
        up_w.write(data)
        await asyncio.gather(_copy(reader, up_w), _copy(up_r, writer))

    def remember(self, name: str, key: str, result: str) -> None:
        row = {"name": name, "address": key, "result": result, "ts": time.time()}
        self.log.append(row)
        self.unsaved.append(row)


class _Prefixed:
    """A stream with two bytes already read put back in front."""

    def __init__(self, reader: asyncio.StreamReader, start: bytes):
        self.reader = reader
        self.start = bytearray(start)

    async def readexactly(self, count: int) -> bytes:
        taken = bytes(self.start[:count])
        del self.start[:count]
        if len(taken) < count:
            taken += await self.reader.readexactly(count - len(taken))
        return taken


# ---------------------------------------------------------------- the add-on
def _jars(folder: Path) -> list[str]:
    try:
        return [p.name.lower().replace("-", "").replace("_", "") for p in folder.glob("*.jar")]
    except OSError:
        return []


def _json_value(path: Path, key: str, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        return None
    return data.get(key, default) if isinstance(data, dict) else None


def _toml_on(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return True  # written on first start; on by default
    except OSError:
        return False
    found = re.search(r"^\s*enabled\s*=\s*(true|false)", text, re.MULTILINE)
    return not found or found[1] == "true"


def proxy_addon(ctx: ServerContext) -> str | None:
    """The add-on (or built-in setting) that makes this server read PROXY
    headers, when it is there and on, or None (the Vanilla fallback).
    research/login-antibot-addons.md has where each keeps its setting."""
    from . import gamesettings

    server_type = ctx.config.server_type
    directory = Path(ctx.config.server_dir)
    if server_type.content == "plugins" and server_type.changes_farms:
        on = gamesettings.read_yaml(directory / "config/paper-global.yml", "proxies.proxy-protocol")
        return "Paper" if on is True else None
    jars = _jars(directory / (server_type.content_folder or "mods"))
    if not server_type.content_folder:
        return None

    def has(word: str) -> bool:
        return any(word in jar for jar in jars)

    config = directory / "config"
    if server_type.id in ("fabric", "quilt"):
        if has("proxyprotocolsupport") and (
            _json_value(config / "proxy_protocol_support.json", "enableProxyProtocol", True) is True
        ):
            return "Proxy Protocol Support"
        if has("haproxycompat") and _json_value(config / "haproxycompat.json", "enabled", True):
            return "HAProxyCompat"
    if server_type.id == "neoforge" and has("haproxycompat"):
        return "HAProxyCompat" if _toml_on(config / "haproxycompat-common.toml") else None
    if server_type.id == "forge" and has("properproxyprotocol"):
        # Its settings file isn't documented where we could read it: the
        # jar alone counts (a hand check in the handoff).
        return "ProperProxyProtocol"
    return None


# ---------------------------------------------------------------- server.properties
RESTORE_KEY = "join_restore"


def move_server(ctx: ServerContext, fallback: bool) -> str:
    """Put the server behind the guard for this run: server-ip 127.0.0.1,
    and with the Vanilla fallback prevent-proxy-connections off (every
    player arrives from 127.0.0.1). The old values are kept to put back.
    Returns the address the guard listens on ("" for every one)."""
    path = Path(ctx.config.server_dir) / FILENAME
    props = PropertiesFile.read(path) if path.is_file() else PropertiesFile()
    restore: dict[str, str] = ctx.db.get_setting(RESTORE_KEY) or {}
    want = {"server-ip": "127.0.0.1"}
    if fallback and (props.get("prevent-proxy-connections") or "").strip() == "true":
        want["prevent-proxy-connections"] = "false"
    elif not fallback and "prevent-proxy-connections" in restore:
        props.set("prevent-proxy-connections", restore.pop("prevent-proxy-connections"))
    for k, v in want.items():
        if k not in restore:
            restore[k] = props.get(k) or ""
        props.set(k, v)
    ctx.db.set_setting(RESTORE_KEY, restore)
    props.write(path)
    host = restore.get("server-ip", "").strip()
    return "" if host in ("", "0.0.0.0", "127.0.0.1") else host


def restore_server(ctx: ServerContext) -> None:
    """Put back what move_server changed, so the server starts direct."""
    restore = ctx.db.get_setting(RESTORE_KEY) or {}
    if not restore:
        return
    path = Path(ctx.config.server_dir) / FILENAME
    if path.is_file():
        props = PropertiesFile.read(path)
        for k, v in restore.items():
            props.set(k, v)
        props.write(path)
    ctx.db.set_setting(RESTORE_KEY, {})


# ---------------------------------------------------------------- the agent side
def why_not(ctx: ServerContext) -> str | None:
    """Why Join protection can't run for this server, or None."""
    if ctx.config.server_type.edition != "java" or not ctx.config.server_type.wakes_on_join:
        return "java_only"
    if ctx.config.server.raw_command and not RAW_COMMAND_OK:
        return "start_command"
    return None


def size(ctx: ServerContext) -> str:
    """Small or big limits: the owner's pick in Technical, else big for a
    community or once 20+ players were online at once within 30 days."""
    picked = ctx.config.server.join_size
    if picked in PRESETS:
        return picked
    peak = ctx.db.get_setting("join_peak") or {}
    if peak.get("count", 0) >= BIG_AT and time.time() - peak.get("at", 0) < PEAK_DAYS * 86400:
        return "big"
    return "big" if ctx.config.server.played_by == "community" else "small"


def limits(ctx: ServerContext) -> dict[str, int]:
    found = dict(PRESETS[size(ctx)])
    for k, v in (ctx.config.server.join_limits or {}).items():
        if k in LIMIT_KEYS and int(v) > 0:
            found[k] = int(v)
    players = launch_players(ctx)
    if players:
        # Launch mode: room for the crowd the owner expects. Shared homes and
        # schools put several players on one address.
        found["connections"] *= 2
        found["pings"] *= 2
        found["new_names"] = max(found["new_names"], players // 2)
        found["slow_rate"] = max(found["slow_rate"], players // 2)
    return found


def launch_players(ctx: ServerContext) -> int:
    s = ctx.config.server
    return int(s.launch_players or 1) if s.launch_until > time.time() else 0


def new_server(ctx: ServerContext) -> bool:
    row = ctx.core.db.server_row(ctx.server_id) or {}
    return time.time() - float(row.get("created_at") or 0) < NEW_SERVER_DAYS * 86400


def set_up(ctx: ServerContext) -> bool:
    """A server that has made its world (Launch mode is for those only)."""
    from .minecraft import properties

    directory = Path(ctx.config.server_dir)
    path = directory / FILENAME
    if not path.is_file():
        return False
    values = PropertiesFile.read(path).values()
    return properties.world_exists(directory, values, ctx.config.server_type)


def known_names(ctx: ServerContext) -> frozenset[str]:
    """Players this server knows: joined before, on the whitelist, or a
    linked account."""
    from .minecraft.playeractions import read_list

    rows = ctx.db.query("SELECT username FROM players WHERE server_id = ?", (ctx.server_id,))
    names = {r["username"].lower() for r in rows}
    rows = ctx.db.query("SELECT name FROM discord_links WHERE server_id = ?", (ctx.server_id,))
    names |= {r["name"].lower() for r in rows}
    listed = read_list(Path(ctx.config.server_dir), "whitelist").get("players") or []
    names |= {str(p.get("name", "")).lower() for p in listed}
    return frozenset(n for n in names if n)


def _shared(ctx: ServerContext) -> str:
    from .discord import layout

    return layout.addresses(ctx.core.db).get(ctx.server_id) or ""


class GuardHub:
    """The guard's own thread and event loop, shared by every server, and
    what the rest of the agent asks it."""

    def __init__(self, core: AgentCore):
        self.core = core
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.agent_loop: asyncio.AbstractEventLoop | None = None
        self.listeners: dict[str, Listener] = {}
        self.direct_next: set[str] = set()
        self.waking: dict[str, float] = {}
        self.failed_at: dict[str, float] = {}
        self._known_at: dict[str, float] = {}
        self._task: asyncio.Task | None = None

    # -- the thread ------------------------------------------------------------
    def _alive(self) -> bool:
        return bool(
            self.thread is not None
            and self.thread.is_alive()
            and self.loop is not None
            and self.loop.is_running()
        )

    def _ensure_thread(self) -> None:
        if self._alive():
            return
        loop = asyncio.new_event_loop()
        running = threading.Event()
        loop.call_soon(running.set)
        thread = threading.Thread(target=loop.run_forever, name="join-guard", daemon=True)
        thread.start()
        running.wait(2)
        self.loop, self.thread = loop, thread

    async def _in_guard(self, coro: Any) -> Any:
        self._ensure_thread()
        assert self.loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), 5)
        except TimeoutError:
            # The guard's loop stopped under us: the next check starts a new one.
            self.thread = None
            raise OSError("the guard didn't answer") from None

    def start(self) -> None:
        self.agent_loop = asyncio.get_running_loop()
        for server_id, ctx in self.core.servers.items():
            for key, block in (ctx.db.get_setting("join_blocks") or {}).items():
                self._rules(server_id).blocks[key] = block
        self._task = asyncio.create_task(self._supervise(), name="join-guard-watch")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        for server_id in list(self.listeners):
            await self.close(server_id)
        self._save_log()
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread = None

    # -- rules kept even while the port is closed --------------------------------
    def _rules(self, server_id: str) -> Rules:
        listener = self.listeners.get(server_id)
        if listener is not None:
            return listener.rules
        kept = self.__dict__.setdefault("_kept", {})
        if server_id not in kept:
            kept[server_id] = Rules()
            ctx = self.core.servers.get(server_id)
            if ctx is not None:
                kept[server_id].on_block = self._saver(ctx)
        return kept[server_id]

    def _saver(self, ctx: ServerContext) -> Callable[[str, dict[str, Any] | None], None]:
        def save(key: str, block: dict[str, Any] | None) -> None:
            saved = ctx.db.get_setting("join_blocks") or {}
            if block is None:
                saved.pop(key, None)
            else:
                saved[key] = block
            ctx.db.set_setting("join_blocks", saved)

        return save

    # -- the port --------------------------------------------------------------
    async def _open(self, ctx: ServerContext, port: int, host: str) -> Listener:
        listener = self.listeners.get(ctx.server_id)
        if listener is not None and (listener.port, listener.host) == (port, host):
            return listener
        if listener is not None:
            await self.close(ctx.server_id)
        listener = Listener(self, ctx.server_id, port, host)
        listener.rules = self._rules(ctx.server_id)
        listener.bind()
        await self._in_guard(listener.serve())
        self.listeners[ctx.server_id] = listener
        self._refresh(ctx, listener)
        return listener

    async def close(self, server_id: str) -> None:
        listener = self.listeners.pop(server_id, None)
        if listener is None:
            return
        self.__dict__.setdefault("_kept", {})[server_id] = listener.rules
        if self._alive() and self.loop is not None:
            self.loop.call_soon_threadsafe(listener.close)
            await asyncio.sleep(0)
        else:
            listener.close()

    def holds(self, ctx: ServerContext) -> bool:
        return ctx.server_id in self.listeners

    async def before_launch(self, ctx: ServerContext) -> list[str]:
        """Called as the server starts: behind the guard (start arguments
        ["--port", private]) or direct ([])."""
        server_id = ctx.server_id
        ctx.server.behind_guard = None
        direct = server_id in self.direct_next
        self.direct_next.discard(server_id)
        if not ctx.config.server.join_protection or why_not(ctx) or direct:
            await self.close(server_id)
            await asyncio.to_thread(restore_server, ctx)
            return []
        public = ctx.core.ports.port_of(ctx).port
        addon = await asyncio.to_thread(proxy_addon, ctx)
        try:
            host = await asyncio.to_thread(move_server, ctx, addon is None)
            listener = await self._open(ctx, public, host)
        except OSError as exc:
            await self.close(server_id)
            await asyncio.to_thread(restore_server, ctx)
            await self.alert(ctx, "port", str(exc))
            return []
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            private = probe.getsockname()[1]
        listener.private, listener.header, listener.mode = private, addon is not None, "proxy"
        listener.status_cache = None
        ctx.server.behind_guard = public
        ctx.db.set_setting("join_addon", addon or "")
        return ["--port", str(private)]

    # -- the supervisor ----------------------------------------------------------
    async def _supervise(self) -> None:
        while True:
            await asyncio.sleep(SUPERVISE_SECONDS)
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - never take the agent down
                log.exception("Join protection's check failed")

    async def check(self) -> None:
        """One pass: restart a stopped guard, open or close each port for
        what its server is doing, end Launch mode, and keep the rules' facts
        fresh."""
        if self.listeners and not self._alive():
            await self._restart()
        for server_id, ctx in list(self.core.servers.items()):
            s = ctx.config.server
            if s.launch_until and s.launch_until <= time.time():
                await self.end_launch(ctx, by_itself=True)
            running = ctx.server.running
            listener = self.listeners.get(server_id)
            asleep = (
                not running
                and s.start_on_join
                and ctx.server.stopped_for_sleep
                and why_not(ctx) in (None, "start_command")
                and server_id not in self.waking
            )
            if listener is not None and not running and server_id not in self.waking:
                if asleep:
                    listener.mode = "asleep"
                else:
                    await self.close(server_id)
                    listener = None
            elif listener is None and asleep:
                try:
                    listener = await self._open(ctx, ctx.core.ports.port_of(ctx).port, "")
                    listener.mode = "asleep"
                except OSError:
                    log.info("the port of %s couldn't be opened for a slept server", ctx.name)
            if server_id in self.waking and (running or time.time() - self.waking[server_id] > 120):
                self.waking.pop(server_id, None)
            if listener is not None:
                self._refresh(ctx, listener)
        self._save_log()

    def _refresh(self, ctx: ServerContext, listener: Listener) -> None:
        rules = listener.rules
        rules.on_block = self._saver(ctx)
        rules.limits = limits(ctx)
        rules.launch = bool(launch_players(ctx))
        rules.slow = rules.launch or new_server(ctx)
        rules.home_only = bool(ctx.config.server.join_home_only)
        listener.version = ctx.server.mc_version or ""
        listener.max_players = ctx.config.server.max_players
        listener.asleep_words = self.words("join.asleep")
        if time.time() - self._known_at.get(ctx.server_id, 0) > KNOWN_SECONDS:
            self._known_at[ctx.server_id] = time.time()
            names = known_names(ctx)
            rules.known = lambda name: name.lower() in names

    async def _restart(self) -> None:
        """The guard's thread stopped: open every port again in a new one."""
        old = dict(self.listeners)
        self.thread = None
        for listener in old.values():
            listener.close()
        self.listeners.clear()
        for server_id, listener in old.items():
            ctx = self.core.servers.get(server_id)
            if ctx is None:
                continue
            try:
                fresh = await self._open(ctx, listener.port, listener.host)
            except OSError as exc:
                self.direct_next.add(server_id)
                await self.alert(ctx, "port", str(exc))
                continue
            fresh.mode, fresh.private, fresh.header = (
                listener.mode,
                listener.private,
                listener.header,
            )
            await self.alert(ctx, "restarted")

    # -- what the guard asks the agent ------------------------------------------
    def wake(self, server_id: str) -> None:
        """A player tried to join a slept server: start it once."""
        if server_id in self.waking or self.agent_loop is None:
            return
        self.waking[server_id] = time.time()
        ctx = self.core.servers.get(server_id)
        if ctx is None:
            return

        async def start() -> None:
            from .minecraft.process import ServerError

            if ctx.server.running or not ctx.server.stopped_for_sleep:
                return
            try:  # the port stays answered until the start takes it (before_launch)
                await ctx.server.start(actor="someone joining")
            except ServerError as exc:
                log.info("a slept server couldn't be woken: %s", exc)
                return
            ctx.db.audit("start_on_join", user="someone joining", target=ctx.name)

        asyncio.run_coroutine_threadsafe(start(), self.agent_loop)

    def words(self, key: str, **values: Any) -> str:
        return WORDS[key].format(**values)

    def failed_open(self, server_id: str, why: str) -> None:
        """A check threw: the player was let through. One alert per 10 minutes."""
        now = time.time()
        if now - self.failed_at.get(server_id, 0) < FAIL_ALERT_EVERY or self.agent_loop is None:
            return
        self.failed_at[server_id] = now
        ctx = self.core.servers.get(server_id)
        if ctx is not None:
            asyncio.run_coroutine_threadsafe(self.alert(ctx, why), self.agent_loop)

    async def alert(self, ctx: ServerContext, why: str, detail: str = "") -> None:
        messages = {
            "port": "Join protection couldn't take the port, so the server starts without it",
            "restarted": "Join protection stopped and was started again",
            "check": "A Join protection check failed, so players were let straight in",
        }
        await ctx.bus.publish(
            Event(
                type="join_protection_off",
                level="warn",
                message=f"{messages[why]} on {ctx.name}.",
                data={"server_name": ctx.name, "why": why, "detail": detail},
            )
        )

    # -- Launch mode ------------------------------------------------------------
    async def start_launch(self, ctx: ServerContext, hours: float, players: int) -> None:
        ctx.config.set("server.launch_until", time.time() + hours * 3600)
        ctx.config.set("server.launch_players", int(players))
        ctx.config.save()

    async def end_launch(self, ctx: ServerContext, by_itself: bool = False) -> None:
        ctx.config.set("server.launch_until", 0)
        ctx.config.set("server.launch_players", 0)
        ctx.config.save()
        if by_itself:
            await ctx.bus.publish(
                Event(
                    type="launch_mode_ended",
                    level="info",
                    message=f"Launch mode ended on {ctx.name}. Join protection is back to normal.",
                    data={"server_name": ctx.name},
                )
            )

    # -- the log -------------------------------------------------------------
    def _save_log(self) -> None:
        """Names and addresses go to the database masked (1.2.x.x), like the
        whitelist's new-address alerts. The real address stays in memory
        only, for "ban-ip <name>" on this run."""
        from .whitelist import mask

        for server_id, listener in self.listeners.items():
            rows, listener.unsaved = listener.unsaved, []
            if not rows:
                continue
            db = self.core.db
            with db._lock:
                db._conn.executemany(
                    "INSERT INTO join_log (server_id, name, address, result, ts) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [
                        (
                            server_id,
                            r["name"],
                            mask(r["address"].split("/")[0]),
                            r["result"],
                            r["ts"],
                        )
                        for r in rows
                    ],
                )
                db._conn.execute(
                    "DELETE FROM join_log WHERE server_id = ? AND id NOT IN (SELECT id FROM "
                    "join_log WHERE server_id = ? ORDER BY id DESC LIMIT ?)",
                    (server_id, server_id, LOG_KEPT),
                )
                db._conn.commit()

    def name_log(self, ctx: ServerContext) -> list[dict[str, Any]]:
        self._save_log()
        return ctx.db.query(
            "SELECT name, address, result, ts FROM join_log WHERE server_id = ? "
            "ORDER BY id DESC LIMIT 100",
            (ctx.server_id,),
        )

    def address_of(self, ctx: ServerContext, name: str) -> str | None:
        """The address a name last joined from on this run, or None."""
        listener = self.listeners.get(ctx.server_id)
        for row in reversed(listener.log if listener else ()):
            if row["name"].lower() == name.lower():
                return str(row["address"])
        return None

    # -- what the apps show ------------------------------------------------------
    def fallback(self, ctx: ServerContext) -> bool:
        """Behind the guard without PROXY headers: the server sees 127.0.0.1."""
        listener = self.listeners.get(ctx.server_id)
        return bool(listener and listener.mode == "proxy" and not listener.header)

    def status(self, ctx: ServerContext) -> dict[str, Any]:
        s = ctx.config.server
        listener = self.listeners.get(ctx.server_id)
        rules = self._rules(ctx.server_id)
        guarding = bool(listener and listener.mode == "proxy")
        return {
            "on": bool(s.join_protection),
            "running": guarding,
            "restart_needed": bool(s.join_protection) != guarding and ctx.server.running,
            "why_not": why_not(ctx),
            "blocked_today": rules.away_today(),
            "size": size(ctx),
            "size_picked": s.join_size,
            "limits": limits(ctx),
            "presets": PRESETS,
            "home_only": bool(s.join_home_only),
            "burst": rules.burst,
            "slow": bool(launch_players(ctx)) or new_server(ctx),
            "launch_until": s.launch_until if s.launch_until > time.time() else None,
            "launch_players": s.launch_players if s.launch_until > time.time() else None,
            "can_launch": set_up(ctx),
            "addon": (ctx.db.get_setting("join_addon") or None) if guarding else None,
            "real_addresses": bool(listener and listener.header) if guarding else None,
            "playit_seen": bool(listener and time.time() - listener.playit_seen < 7 * 86400),
            "start_on_join": bool(s.start_on_join),
            "shared_address": _shared(ctx),
            "asleep": bool(listener and listener.mode == "asleep"),
        }

    def technical(self, ctx: ServerContext) -> dict[str, Any]:
        return {
            "blocks": self._rules(ctx.server_id).block_list(),
            "log": self.name_log(ctx),
        }

    def block(self, ctx: ServerContext, address: str, user: str) -> str:
        key = address_key(address)
        self._rules(ctx.server_id).block(key, None, "owner", user)
        ctx.db.audit("join_block", user=user, target=key)
        return key

    def unblock(self, ctx: ServerContext, address: str, user: str) -> bool:
        key = address_key(address)
        done = self._rules(ctx.server_id).unblock(key)
        if done:
            ctx.db.audit("join_unblock", user=user, target=key)
        return done

    def note_peak(self, ctx: ServerContext) -> None:
        """Keep the most players measured online at once in 30 days."""
        count = ctx.players.online_count()
        if count is None:
            return
        peak = ctx.db.get_setting("join_peak") or {}
        old = time.time() - peak.get("at", 0) >= PEAK_DAYS * 86400
        if old or count > peak.get("count", 0):
            ctx.db.set_setting("join_peak", {"count": count, "at": time.time()})
