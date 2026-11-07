"""Diagnose server: the join with all mods.

The test player (agent/joinbot.py) stops at a modded server's mod check. So
this join runs the real Minecraft game through HeadlessMC
(agent/headlessmc.py), with no window, with the mods players need: the
server's turned-on mods minus the ones marked server-only. It only runs
when the owner said yes ("about 3 GB of free memory, up to 20 minutes").

  1. Join with all of them. The server's own console says whether the game
     joined (and stayed 10 seconds) or was turned away and why.
  2. When the game closes before it even reaches the server, one of the
     mods stops it. Split the list in halves (each half with the mods it
     needs) and keep the half the game won't start with, until one mod is
     left. Then check it: the game won't start with it alone, and starts
     with all the others. Halving that doesn't find one mod says so (it may
     be two mods together).
  3. Try that mod's other versions from the site it came from, for the same
     Minecraft version and loader, newest first, a few at most. A version
     the game starts with is offered as a safe change; none: "Turn off" the
     mod, a risky change (agent/diagnosefix.py). Nothing is deleted.

A server turning the game away (a mod missing on one side, say) isn't
halved: removing mods can only make that worse. The line says what the
server said.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .monitoring.answering import server_ip
from .security.paths import safe_join
from .whitelist import online_mode

if TYPE_CHECKING:
    from .core import ServerContext
    from .diagnose import Run
    from .jobs import JobHandle
    from .mods.jarinfo import ModInfo

GROUP = "Join with all mods"
ASK = (
    "The join with all mods starts the real Minecraft game with your server's mods, "
    "without a window. It needs about 3 GB of free memory and can take up to 20 minutes. "
    "Run it too?"
)
SIGN_IN_FIRST = (
    "Not checked: this server checks accounts, so the game needs one. "
    "Sign in for the join with all mods first."
)
# HeadlessMC's names for the loaders it can start.
LOADERS = {"fabric": "fabric", "forge": "forge", "neoforge": "neoforge"}
FREE_MB = 3 * 1024
GAME_MEMORY = "-Xmx2G"
TOTAL_TIME = 20 * 60.0
FIRST_TRY = 10 * 60.0  # the first one downloads the game and its Java
TRY = 4 * 60.0
STAY = 10.0
POLL = 1.0  # how often the server's console is read during a try
VERSIONS = 3  # other versions tried at most
JOINED = re.compile(r"^(\S+) joined the game")
LOST = re.compile(r"^(\S+?)(?: \(/[^)]*\))? lost connection: (.*)$")
CRASH_WORDS = re.compile(r"exception|error|crash", re.IGNORECASE)


@dataclass
class Try:
    joined: bool
    # False: the game closed (or hung) before it reached the server.
    reached: bool
    why: str


def wanted(ctx: ServerContext) -> bool:
    """Is there a join with all mods to offer? A mod server with mods players need."""
    return ctx.config.server_type.content == "mods" and bool(players_mods(ctx))


def players_mods(ctx: ServerContext) -> list[ModInfo]:
    return [m for m in ctx.mods.scan() if m.enabled and m.environment != "server"]


def with_needs(chosen: list[ModInfo], every: list[ModInfo]) -> list[ModInfo]:
    """``chosen`` plus the mods they need, from ``every``, in ``every``'s order."""
    by_id: dict[str, ModInfo] = {}
    for mod in every:
        for inner in (mod, *mod.bundled):
            by_id.setdefault(inner.mod_id, mod)
    keep = {id(m) for m in chosen}
    todo = list(chosen)
    while todo:
        mod = todo.pop()
        for dep in (*mod.dependencies, *(d for b in mod.bundled for d in b.dependencies)):
            found = by_id.get(dep.mod_id) if dep.kind == "depends" else None
            if found is not None and id(found) not in keep:
                keep.add(id(found))
                todo.append(found)
    return [m for m in every if id(m) in keep]


async def find_broken(
    mods: list[ModInfo], crashes: Callable[[list[ModInfo]], Awaitable[bool | None]]
) -> ModInfo | None:
    """Halve ``mods`` down to the one the game won't start with. ``crashes``
    answers None when time ran out. None: no single mod found."""
    suspects = list(mods)
    while len(suspects) > 1:
        half = suspects[: len(suspects) // 2]
        answer = await crashes(half)
        if answer is None:
            return None
        suspects = half if answer else suspects[len(half) :]
    if not suspects:
        return None
    suspect = suspects[0]
    # Checked both ways, or two mods that only break together would be
    # blamed on one: with it alone (and what it needs) the game won't
    # start, without it (and what needs it) the game starts.
    if await crashes([suspect]) is not True:
        return None
    without = [m for m in mods if not any(n is suspect for n in with_needs([m], mods))]
    return suspect if await crashes(without) is False else None


# ------------------------------------------------------------ one try
async def attempt(ctx: ServerContext, jars: list[Path], timeout: float) -> Try:
    """Start the game with ``jars`` in its mods folder and join this server."""
    hmc = ctx.core.headlessmc
    offline = online_mode(ctx) is False
    name = None if offline else hmc.name()
    mods_dir = hmc.game_dir / "mods"
    shutil.rmtree(mods_dir, ignore_errors=True)
    mods_dir.mkdir(parents=True)
    for jar in jars:
        shutil.copy2(jar, safe_join(mods_dir, jar.name))
    command = [
        "launch",
        LOADERS[ctx.config.server_type.id],
        str(ctx.server.mc_version),
        "--headless",
        f"--server={server_ip(ctx)}:{ctx.core.ports.port_of(ctx).port}",
        f"--jvm={GAME_MEMORY}",
        *(["--offline"] if offline else []),
    ]
    before = {p["username"].lower() for p in ctx.players.online()}
    last = ctx.server.console.tail(1)
    seq = last[0].seq if last else 0
    output: list[str] = []
    if name:
        ctx.players.ignored.add(name)
    try:
        async with hmc.started(*command) as process:
            assert process.stdout is not None
            stdout = process.stdout

            async def read() -> None:
                async for raw in stdout:
                    output.append(raw.decode("utf-8", "replace").strip())
                    del output[:-40]

            reader = asyncio.create_task(read())
            try:
                return await _watch(ctx, process, name, before, seq, output, timeout)
            finally:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader
    finally:
        if name:
            await asyncio.sleep(2.0)  # its "left" line is skipped too
            ctx.players.ignored.discard(name)


async def _watch(ctx, process, name, before, seq, output, timeout) -> Try:
    end = time.monotonic() + timeout
    who: str | None = None
    joined_at: float | None = None
    while time.monotonic() < end:
        for line in ctx.server.console.since(seq, limit=2000):
            seq = line.seq
            text = line.message or line.raw
            if found := JOINED.match(text):
                player = found.group(1)
                if (name and player.lower() == name.lower()) or (
                    not name and player.lower() not in before
                ):
                    who, joined_at = player, time.monotonic()
            elif found := LOST.match(text):
                player = found.group(1)
                ours = player.lower() == (who or name or "").lower() or (
                    not name and not who and player.lower() not in before
                )
                if ours:
                    said = found.group(2).strip()
                    if joined_at is None:
                        return Try(False, True, f"The server said: {said}")
                    return Try(False, True, f"It was dropped after joining: {said}")
        if joined_at is not None and time.monotonic() - joined_at >= STAY:
            return Try(True, True, "Joined and stayed for 10 seconds.")
        if process.returncode is not None and joined_at is None:
            return Try(False, False, _crash_why(output))
        await asyncio.sleep(POLL)
    return Try(False, False, f"The game didn't reach the server within {timeout / 60:g} minutes.")


def _crash_why(output: list[str]) -> str:
    said = next((line for line in reversed(output) if CRASH_WORDS.search(line)), "")
    if said:
        return f"The game closed before it reached the server. It said: {said[:300]}"
    return "The game closed before it reached the server."


# ------------------------------------------------------------ the run
async def check(out: Run, job: JobHandle) -> None:
    """The join with all mods, then the broken mod and its other versions."""
    ctx = out.ctx
    mods = players_mods(ctx)
    why = _not_checked(ctx)
    if why:
        out.line(GROUP, f"Join with all {len(mods)} mods", None, why)
        out.result["sign_in_mods"] = out.result.get("sign_in_mods") or why == SIGN_IN_FIRST
        return
    end = time.monotonic() + TOTAL_TIME
    first = [True]

    async def run(jars: list[Path]) -> Try | None:
        left = end - time.monotonic()
        if left < 30:
            return None
        timeout = min(FIRST_TRY if first[0] else TRY, left)
        first[0] = False
        return await attempt(ctx, jars, timeout)

    from .headlessmc import HeadlessMcError

    job.step(f"Starting Minecraft with all {len(mods)} mods (HeadlessMC)")
    try:
        full = await run([Path(m.path) for m in mods])
    except (HeadlessMcError, OSError) as exc:
        out.line(GROUP, f"Join with all {len(mods)} mods", False, str(exc))
        return
    assert full is not None
    out.line(GROUP, f"Join with all {len(mods)} mods", full.joined, full.why)
    if full.joined or full.reached:
        return
    tries = [0]

    async def crashes(chosen: list[ModInfo]) -> bool | None:
        tries[0] += 1
        job.step(f"Finding the mod that stops the game (try {tries[0]})")
        found = await run([Path(m.path) for m in with_needs(chosen, mods)])
        return None if found is None else not found.reached

    try:
        broken = await find_broken(mods, crashes)
    except (HeadlessMcError, OSError) as exc:
        out.line(GROUP, "Find the mod that stops the game", False, str(exc))
        return
    if broken is None:
        out.line(
            GROUP,
            "Find the mod that stops the game",
            None,
            "Not found: no single mod stops it (it may be two mods together), "
            "or the 20 minutes ran out.",
        )
        return
    out.line(
        GROUP,
        "Find the mod that stops the game",
        False,
        f"It's {broken.name or broken.mod_id} ({broken.filename}): "
        "the game starts with all the other mods.",
    )
    found = out.result["lines"][-1]
    found.update({"broken": broken.filename, "mod_id": broken.mod_id, "name": broken.name})
    await _other_versions(ctx, out, job, mods, broken, found, run)


async def _other_versions(ctx, out, job, mods, broken, found, run) -> None:
    from .mods.manager import ModError, refuse_known_bad
    from .mods.sources import LABELS, SourceError

    what = f"Try other versions of {broken.name or broken.mod_id}"
    source, project = ctx.mods.origin(broken)
    if source not in ("modrinth", "curseforge") or not project:
        out.line(GROUP, what, None, f"Not checked: it came from {LABELS.get(source, source)}.")
        return
    try:
        versions = await ctx.mods.site(source).versions(project, ctx.server.mc_version)
    except (SourceError, ModError) as exc:
        out.line(GROUP, what, None, f"Not checked: {exc}")
        return
    others = [v for v in versions if v["version_number"] != broken.version][:VERSIONS]
    if not others:
        out.line(GROUP, what, None, f"Not checked: {LABELS[source]} has no other version for it.")
        return
    folder = ctx.core.headlessmc.folder / "candidates"
    rest = [Path(m.path) for m in mods if m is not broken]
    for version in others:
        job.step(f"{what}: {version['version_number']}")
        try:
            data, filename, _sha = await ctx.mods.site(source).download(version)
            refuse_known_bad(data, filename)
        except (SourceError, ModError) as exc:
            out.line(GROUP, f"{what}: {version['version_number']}", None, str(exc))
            continue
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        jar = safe_join(folder, filename)
        jar.write_bytes(data)
        tried = await run([*rest, jar])
        if tried is None:
            out.line(GROUP, what, None, "Not checked: the 20 minutes ran out.")
            return
        if tried.reached:
            out.line(GROUP, what, True, f"The game starts with {version['version_number']}.")
            found.update(
                {"version_id": version["version_id"], "version": version["version_number"]}
            )
            return
    out.line(
        GROUP,
        what,
        False,
        f"The game didn't start with {', '.join(v['version_number'] for v in others)} either.",
    )


def _not_checked(ctx: ServerContext) -> str | None:
    import psutil

    kind = ctx.config.server_type
    if kind.id not in LOADERS:
        return f"Not checked: HeadlessMC can't start {kind.name} yet."
    if not ctx.server.mc_version:
        return "Not checked: start the server once so the app knows its Minecraft version."
    free = psutil.virtual_memory().available / (1024 * 1024)
    if free < FREE_MB:
        return (
            f"Not checked: it needs about 3 GB of free memory and this PC has "
            f"{free / 1024:.1f} GB free. Close some programs and try again."
        )
    if online_mode(ctx) is not False and not ctx.core.headlessmc.name():
        return SIGN_IN_FIRST
    return None


def broken_lines(result: dict[str, Any]) -> list[dict[str, Any]]:
    """The lines naming a mod the game won't start with (agent/diagnosefix.py)."""
    return [x for x in result["lines"] if x["group"] == GROUP and x.get("broken")]
