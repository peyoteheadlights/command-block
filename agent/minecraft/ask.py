"""Asking the running server and reading its answer from the console.

Used where a value must come from the server itself, never guessed per
version: the console's command list (``help``) and the game rules screen
(agent/gamerules.py). The questions are sent as internal commands, so the
console doesn't show the "> help" line, only Minecraft's answer.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable

COLOR_RE = re.compile(r"§.")  # Bedrock colours its help pages
QUIET = 0.8  # seconds without a new answer line: the server is done answering
WAIT = 5.0  # seconds at most for the first answer
MAX_PAGES = 30  # Bedrock's help is split in pages; more than this is not a help list
# Java (brigadier): "/gamerule keepInventory [<value>]". Bedrock: "/ability <player: target> ...".
USAGE_RE = re.compile(r"^/([a-z0-9_:.-]+)(?:\s.*)?$", re.IGNORECASE)
PAGE_RE = re.compile(r"page \d+ of (\d+)", re.IGNORECASE)

_locks: dict[int, asyncio.Lock] = {}
# The command list read once per server start: {server_id: (started_at, list)}.
_commands: dict[str, tuple[float | None, list[dict]]] = {}


async def ask(server, commands: list[str], wanted: Callable[[str], bool]) -> list[str]:
    """Send ``commands`` and return the server's answer lines that ``wanted``
    accepts, colour codes taken out. Stops once the server has been quiet for
    a moment after answering, or after WAIT seconds with no answer at all.
    One question at a time per server, so two answers never mix."""
    lock = _locks.setdefault(id(server), asyncio.Lock())
    async with lock:
        last = server.console.tail(1)
        after = last[0].seq if last else 0
        for command in commands:
            await server.send_command(command, internal=True)
        found: list[str] = []
        start = heard = time.monotonic()
        while True:
            await asyncio.sleep(0.2)
            for line in server.console.since(after):
                after = line.seq
                if line.source != "stdout":
                    continue
                said = COLOR_RE.sub("", line.message or line.raw).strip()
                if wanted(said):
                    found.append(said)
                    heard = time.monotonic()
            now = time.monotonic()
            if (found and now - heard >= QUIET) or (not found and now - start >= WAIT):
                return found


def parse_help(lines: list[str]) -> list[dict]:
    """Each command once, by name, with the first usage line seen for it."""
    found: dict[str, str] = {}
    for line in lines:
        match = USAGE_RE.match(line)
        if match:
            found.setdefault(match.group(1).lower(), line)
    return [{"name": name, "usage": usage} for name, usage in sorted(found.items())]


async def command_list(ctx) -> list[dict]:
    """The commands this server says it has, read once per start. Empty when
    it isn't running or didn't answer: the apps then complete nothing."""
    server = ctx.server
    if not server.running or server.state.value != "ONLINE":
        return []
    cached = _commands.get(ctx.server_id)
    if cached and cached[0] == server.started_at and cached[1]:
        return cached[1]
    wanted = lambda s: bool(USAGE_RE.match(s) or PAGE_RE.search(s))  # noqa: E731
    lines = await ask(server, ["help"], wanted)
    pages = next((int(m.group(1)) for s in lines if (m := PAGE_RE.search(s))), 1)
    if pages > 1:  # Bedrock: page 1 came with "help", ask for the rest
        more = [f"help {n}" for n in range(2, min(pages, MAX_PAGES) + 1)]
        lines += await ask(server, more, wanted)
    found = parse_help(lines)
    _commands[ctx.server_id] = (server.started_at, found)
    return found
