"""Is the server really answering, or only running?

"Online" means the process is alive and printed "Done" once. A server can
stay like that while nobody can join: the game is frozen, it ran out of
memory, or its port stopped answering. So while a server is online this
asks it two things, the way a player and an owner would:

  game port  the same "server list ping" the game sends to show a server in
             its list (RakNet's ping for Bedrock). Once a minute, a few bytes.
  console    "list", every fifth check, or every check once something
             failed. The game loop answers console commands, so no answer
             means the game itself is stuck even if the port still answers.

Three missed checks in a row (about three minutes, so one slow save never
counts) and the server is "not answering": the apps say so, an alert goes
out with a plain-words reason built only from what was seen, and, when
automatic restart is on, the app takes a backup and restarts the server. No
backup, no automatic restart: the world is never put at risk to fix it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import struct
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from ..events import Event
from ..minecraft import raknet
from ..minecraft.analyzer import analyze
from ..minecraft.console import BEDROCK_LIST_RE, PLAYER_LIST_RE

if TYPE_CHECKING:
    from ..core import ServerContext

log = logging.getLogger("msc.answering")

CHECK_EVERY = 60.0  # seconds between checks
MISSES = 3  # failed checks in a row before it counts
CONSOLE_EVERY = 5  # the console is asked every fifth check while all is well
CONSOLE_WAIT = 20.0  # seconds a "list" answer may take
# A server that keeps getting stuck is restarted at most this often by
# itself; after that the owner decides.
MAX_FIXES = 3
FIX_WINDOW = 6 * 3600
CANT_KEEP_UP_RE = re.compile(r"Can't keep up!")


def _varint(value: int) -> bytes:
    value &= 0xFFFFFFFF
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _read_varint(stream: Any) -> int:
    value = 0
    for shift in range(0, 35, 7):
        byte = stream.read(1)
        if not byte:
            raise ConnectionError("closed")
        value |= (byte[0] & 0x7F) << shift
        if not byte[0] & 0x80:
            return value
    raise ValueError("varint too long")


def java_ping(host: str, port: int, timeout: float = 3.0) -> dict[str, Any]:
    """Ask a Java server for its server list entry.

    ``connected`` says whether the port took the connection at all, and
    ``answer`` is the server's own reply (None when it gave none), so a
    port nobody listens on and a server that is too stuck to answer read
    differently.
    """
    result: dict[str, Any] = {"connected": False, "answer": None}
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError:
        return result
    result["connected"] = True
    try:
        with sock, sock.makefile("rb") as stream:
            name = host.encode("utf-8")
            # Handshake (protocol -1: "just asking"), next state 1 (status),
            # then the status request.
            body = b"\x00" + _varint(-1) + _varint(len(name)) + name
            body += struct.pack(">H", port) + _varint(1)
            sock.sendall(_varint(len(body)) + body + b"\x01\x00")
            if not 0 < _read_varint(stream) <= 1 << 20 or _read_varint(stream) != 0:
                return result
            size = _read_varint(stream)
            text = stream.read(size) if size <= 1 << 20 else b""
            status = json.loads(text.decode("utf-8"))
    except (OSError, ValueError):
        return result
    if not isinstance(status, dict):
        return result
    players = status.get("players") or {}
    result["answer"] = {
        "version": (status.get("version") or {}).get("name"),
        "players": players.get("online"),
        "max_players": players.get("max"),
    }
    return result


def ping(protocol: str, host: str, port: int) -> dict[str, Any]:
    """The game port's answer, whichever edition this server is."""
    if protocol == "raknet":
        answer = raknet.ping(host, port, timeout=3.0)
        # UDP has no connection, so whether something is there is unknown.
        return {"connected": None, "answer": answer}
    return java_ping(host, port)


def server_ip(ctx: ServerContext) -> str:
    """Where the game listens: server-ip from server.properties when it is
    set (Minecraft then listens only there), this PC otherwise."""
    path = ctx.config.server_dir / "server.properties"
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "server-ip" and value.strip():
                return value.strip()
    except OSError:
        pass
    return "127.0.0.1"


def diagnose(
    ctx: ServerContext, game: dict[str, Any], console: bool | None, port: int
) -> dict[str, Any]:
    """What is wrong, in plain words, from what was actually seen."""
    if game["answer"] is None and console is False:
        what = "The server is stuck: it doesn't answer players or its console."
    elif game["answer"] is None and game["connected"] is False:
        what = (
            f"Nothing answers on port {port} any more, so players get "
            '"Connection refused". The game itself still answers its console.'
        )
    elif game["answer"] is None:
        what = (
            f"Port {port} takes the connection but the server doesn't answer it, "
            "so players time out. The game itself still answers its console."
        )
    else:
        what = (
            "The game is frozen: it still shows in the server list, but it doesn't "
            "answer its console, so nobody can join."
        )

    why: list[str] = []
    lines = [ln.raw for ln in ctx.server.console.tail(300)]
    found = analyze(lines)
    if found.category in ("OutOfMemoryError", "JavaHeapError"):
        why.append(
            "It ran out of memory (the console says OutOfMemoryError). Give it more "
            "memory in Settings, or remove heavy mods."
        )
    behind = sum(1 for raw in lines if CANT_KEEP_UP_RE.search(raw))
    if behind:
        why.append(
            f"It fell behind {behind} time{'s' if behind != 1 else ''} recently "
            '("Can\'t keep up!"), so this PC may be too slow for it right now.'
        )
    snap = ctx.metrics.last or {}
    th = ctx.config.thresholds
    if (snap.get("ram_percent") or 0) >= th.ram_percent:
        why.append(f"This PC's memory is {snap['ram_percent']:.0f}% full.")
    if (snap.get("cpu_percent") or 0) >= th.cpu_percent:
        why.append(f"This PC's processor is {snap['cpu_percent']:.0f}% busy.")
    free = snap.get("disk_free_gb")
    if free is not None and free <= th.disk_free_gb:
        why.append(f"Only {free:.1f} GB is free on the server's drive.")
    last = ctx.server.console.tail(1)
    if console is False and last:
        quiet = (time.time() - last[0].ts) / 60
        if quiet >= 2:
            why.append(f"The console has printed nothing for {quiet:.0f} minutes.")
    if not why:
        why.append("The app couldn't see why from the console or this PC's numbers.")
    return {
        "what": what,
        "why": why,
        "game_port": "answers" if game["answer"] is not None else "no answer",
        "port_connects": game["connected"],
        "console": {True: "answers", False: "no answer", None: "not asked"}[console],
        "port": port,
    }


class AnswerWatch:
    """Checks one server while it is online. See the module docstring."""

    def __init__(self, ctx: ServerContext):
        self.ctx = ctx
        self.problem: dict[str, Any] | None = None
        self.checked_at: float | None = None
        self.last_ok: bool | None = None
        self._misses = 0
        self._checks = 0
        self._fixes: deque[float] = deque(maxlen=MAX_FIXES)
        self._task: asyncio.Task | None = None

    def status(self) -> dict[str, Any]:
        """``ok`` is None until a check ran: never a guessed yes."""
        return {"ok": self.last_ok, "checked_at": self.checked_at, "problem": self.problem}

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"answering-{self.ctx.server_id}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(CHECK_EVERY)
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("answer check failed")

    def _skip(self) -> bool:
        """Not judged while it isn't meant to answer: not online, or a change
        (a restore, a backup) is holding it."""
        server = self.ctx.server
        return (
            server.state.value != "ONLINE"
            or bool(server.held_by)
            or self.ctx.core.jobs.risky_job(self.ctx.server_id) is not None
        )

    async def ask_console(self) -> bool:
        """Send "list" and wait for the game loop's answer."""
        server = self.ctx.server
        last = server.console.tail(1)
        after = last[0].seq if last else 0
        try:
            await server.send_command("list", internal=True)
        except Exception:
            return False
        deadline = time.monotonic() + CONSOLE_WAIT
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            for line in server.console.since(after):
                said = (line.message or line.raw).strip()
                if line.source == "stdout" and (
                    PLAYER_LIST_RE.match(said) or BEDROCK_LIST_RE.match(said)
                ):
                    return True
        return False

    async def check(self) -> dict[str, Any]:
        """One check; the third miss in a row decides."""
        if self._skip():
            self._misses = 0
            if self.problem and self.ctx.server.state.value != "ONLINE":
                self.problem = None  # stopped or restarting: a new start is checked afresh
            self.last_ok = None
            return self.status()
        ctx = self.ctx
        self._checks += 1
        port = ctx.core.ports.port_of(ctx).port
        game = await asyncio.to_thread(ping, ctx.config.server_type.ping, server_ip(ctx), port)
        console: bool | None = None
        if game["answer"] is None or self._misses or self._checks % CONSOLE_EVERY == 0:
            console = await self.ask_console()
        self.checked_at = time.time()
        if game["answer"] is not None and console is not False:
            self._misses = 0
            self.last_ok = True
            if self.problem:
                self.problem = None
                await ctx.bus.publish(
                    Event(
                        type="server_answering_again",
                        level="success",
                        message="The server answers again",
                    )
                )
            return self.status()
        self._misses += 1
        if self._misses < MISSES or self.problem:
            return self.status()
        found = diagnose(ctx, game, console, port)
        self.last_ok = False
        self.problem = {**found, "since": self.checked_at, "fix": await self.fix()}
        await ctx.bus.publish(
            Event(
                type="server_not_answering",
                level="error",
                message=" ".join([found["what"], *found["why"], self.problem["fix"]]),
                data=self.problem,
            )
        )
        return self.status()

    async def fix(self) -> str:
        """Backup, then restart, when automatic restart is on. Says what it did."""
        ctx, server = self.ctx, self.ctx.server
        if not ctx.config.monitor.auto_restart:
            return "Automatic restart is off. Press Restart to fix it."
        if server.maintenance and ctx.config.maintenance.block_auto_restart:
            return "Maintenance mode is on, so it wasn't restarted by itself. Press Restart."
        cutoff = time.time() - FIX_WINDOW
        if len([t for t in self._fixes if t >= cutoff]) >= MAX_FIXES:
            return (
                f"It got stuck {MAX_FIXES} times in {FIX_WINDOW // 3600} hours, so the app "
                "stopped restarting it by itself. Press Restart after looking at the reason."
            )
        try:
            await ctx.backups.create(
                kind="safety",
                user="answer check",
                note="Before restarting a server that stopped answering",
            )
        except Exception as exc:
            return (
                f"The app couldn't take a backup first ({exc}), so it didn't restart the "
                "server by itself. Press Restart to restart it anyway."
            )
        self._fixes.append(time.time())
        asyncio.create_task(self._restart(), name=f"answering-fix-{ctx.server_id}")
        return "The app took a backup and is restarting the server."

    async def _restart(self) -> None:
        try:
            await self.ctx.server.restart(actor="answer check")
        except Exception as exc:
            await self.ctx.bus.publish(
                Event(
                    type="server_restart_failed",
                    level="error",
                    message=f"Restart failed: {exc}",
                )
            )
