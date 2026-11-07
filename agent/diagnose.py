"""Diagnose server: join the server like a player would and say what happened.

Runs only when someone clicks "Diagnose server" (weak PCs first), as a job.
A stopped server is started (the click is the yes). Then the test player
(agent/joinbot.py) joins from this PC and every step it gets through, or the
step it stopped at and why, becomes a line in the result. The result never
says "healthy": it lists what was tried and what happened.

The joins so far, all from this PC: an offline-mode server as the
``CommandBlock`` test player; an online-mode Java server with the owner's
Java account, a Bedrock server (and Bedrock players through Geyser) with the
owner's Xbox account, after "Sign in with Microsoft" (agent/microsoft.py).
Then the other ways in (home network, Tailscale, the internet, playit) are
asked whether the game port answers (agent/waysin.py). On a mod server,
when the owner said yes, the real game joins with all its mods
(agent/fullmods.py).

After the joins, the changes that may fix what failed are offered
(agent/diagnosefix.py). Applying them runs the joins again as the next
round of the same run, keeping its list of changes made.

The last result is kept on the server (``ctx.diagnosis``) and updated while
it runs, so the apps can show the list live.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import joinbot
from .crossplay import USERNAME_PREFIX
from .minecraft.playeractions import read_list
from .minecraft.properties import PropertiesFile
from .minecraft.state import ServerState
from .monitoring.answering import server_ip
from .whitelist import online_mode

if TYPE_CHECKING:
    from .core import ServerContext
    from .jobs import JobHandle

TEST_PLAYER = "CommandBlock"
NOTICE = "Command Block is testing the server: a test player joins for about a minute."
START_WAIT = 600.0  # how long a started server may take to be online
STEP_WORDS = {
    "connect": "Reach the game port",
    "login": "Log in",
    "spawn": "Arrive in the world",
    "chunks": "Load the world around the player",
    "chat": "Send a chat line and see it come back",
    "stay": "Stay for 10 seconds",
    "leave": "Leave",
}
# Kept a little after the test player leaves, so its "left" line is skipped too.
LEAVE_GRACE = 5.0
SERVER_GROUP = "The server"
SIGN_IN_FIRST = "Not checked: this join needs your Microsoft account. Sign in with Microsoft first."


@dataclass
class Join:
    """One way the test player joins: its heading, the game it speaks, the
    port, and whether it uses the owner's Microsoft account."""

    group: str
    edition: str
    port: int
    microsoft: bool


class Run:
    """One Diagnose run's list, shared live through ``ctx.diagnosis``."""

    def __init__(
        self,
        ctx: ServerContext,
        by: str,
        before: dict[str, Any] | None = None,
        mods: bool = False,
    ):
        """``before``: the round this one follows, after its changes were
        picked. ``mods``: the owner said yes to the join with all mods."""
        self.ctx = ctx
        self.result: dict[str, Any] = {
            "running": True,
            "started_at": time.time(),
            "finished_at": None,
            "by": by,
            "lines": [],
            # True when a join wasn't tried because nobody signed in with Microsoft.
            "sign_in": False,
            "round": before["round"] + 1 if before else 1,
            # Offered changes waiting for Apply all / Apply safe / Deny all.
            "changes": [],
            # What happened to each change picked or denied, every round.
            "done": list(before["done"]) if before else [],
            # Keys of changes already picked or denied: never offered again.
            "tried": list(before["tried"]) if before else [],
            # What only the owner can do (a router port), one line each.
            "steps": [],
            # The join with all mods runs (agent/fullmods.py), every round.
            "mods": before.get("mods", False) if before else mods,
            # True when it wasn't tried: nobody signed in to HeadlessMC.
            "sign_in_mods": False,
        }
        ctx.diagnosis = self.result

    def line(self, group: str, what: str, ok: bool | None, detail: str | None = None) -> None:
        """ok: True worked, False failed, None not checked (with why in detail)."""
        self.result["lines"].append({"group": group, "what": what, "ok": ok, "detail": detail})

    def finish(self) -> dict[str, Any]:
        self.result["running"] = False
        self.result["finished_at"] = time.time()
        return self.result


def _whitelist_on(ctx: ServerContext) -> bool:
    path = ctx.config.server_dir / "server.properties"
    try:
        value = PropertiesFile.read(path).get("white-list") if path.is_file() else None
    except (OSError, ValueError):
        return False
    return (value or "").strip().lower() == "true"


def _on_whitelist(ctx: ServerContext, name: str) -> bool:
    found = read_list(ctx.config.server_dir, "whitelist")["players"] or []
    return name.lower() in {p["name"].lower() for p in found}


async def _get_online(ctx: ServerContext, run: Run, job: JobHandle) -> bool:
    server = ctx.server
    group = SERVER_GROUP
    if server.state == ServerState.ONLINE:
        return True
    if server.state in (ServerState.OFFLINE, ServerState.CRASHED):
        job.step("Starting the server")
        try:
            await server.start(actor=f"Diagnose server ({run.result['by']})")
        except Exception as exc:
            run.line(group, "Start the server", False, str(exc))
            return False
        started = "It was stopped, so Diagnose started it."
    elif server.state == ServerState.STARTING:
        started = "It was starting already."
    else:
        run.line(
            group,
            "Start the server",
            False,
            f"The server is {server.state.value.lower().replace('_', ' ')}. "
            "Try again once it's online or stopped.",
        )
        return False
    job.step("Waiting for the server to be online")
    if not await _online_or_stopped(ctx):
        run.line(
            group,
            "Start the server",
            False,
            "It didn't finish starting. The console and the crash report say why.",
        )
        return False
    run.line(group, "Start the server", True, started)
    return True


async def _online_or_stopped(ctx: ServerContext) -> bool:
    """Wait until the server is online (True), or stops while starting, or
    takes too long (False). A server that stops at once (the EULA) isn't
    waited on for ten minutes."""
    online = asyncio.ensure_future(ctx.server.wait_online(timeout=START_WAIT))
    stopped = asyncio.ensure_future(ctx.server.wait_exit(timeout=START_WAIT))
    try:
        await asyncio.wait({online, stopped}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stopped.cancel()
        if not online.done():
            online.cancel()
    return online.done() and not online.cancelled() and online.result()


def joins(ctx: ServerContext) -> list[Join]:
    """The joins this server gets, from its own settings."""
    port = ctx.core.ports.port_of(ctx).port
    offline = online_mode(ctx) is False
    if ctx.config.server_type.edition == "bedrock":
        if offline:
            return [Join(f"Join from this PC as {TEST_PLAYER}", "bedrock", port, False)]
        return [Join("Join from this PC with your Xbox account", "bedrock", port, True)]
    found = [
        Join(f"Join from this PC as {TEST_PLAYER}", "java", port, False)
        if offline
        else Join("Join from this PC with your Java account", "java", port, True)
    ]
    geyser = ctx.core.ports.bedrock_port_of(ctx)
    if geyser:  # crossplay is on
        found.append(
            Join("Join from this PC as a Bedrock player (Geyser)", "bedrock", geyser.port, True)
        )
    return found


def player_name(ctx: ServerContext, join: Join) -> str | None:
    """The name the server sees for this join, or None when it isn't known."""
    if not join.microsoft:
        return TEST_PLAYER
    names = ctx.core.microsoft.names()
    if join.edition == "java":
        return names["java"]
    if names["gamertag"] and ctx.config.server_type.edition != "bedrock":
        # Floodgate shows Bedrock players as ".Name", spaces made "_".
        return USERNAME_PREFIX + names["gamertag"].replace(" ", "_")
    return names["gamertag"]


def owner_playing(ctx: ServerContext) -> str | None:
    """The owner's name when they're on the server and a join would use
    their account (it kicks them out for about a minute), else None."""
    if ctx.core.microsoft.account() is None:
        return None
    online = {p["username"].lower() for p in ctx.players.online()}
    for join in joins(ctx):
        name = player_name(ctx, join) if join.microsoft else None
        if name and name.lower() in online:
            return name
    return None


def _not_checked(ctx: ServerContext, join: Join) -> str | None:
    """Why this join can't run, or None when it can."""
    if not join.microsoft:
        return None
    if ctx.core.microsoft.account() is None:
        return SIGN_IN_FIRST
    if join.edition == "java" and not ctx.core.microsoft.names()["java"]:
        return (
            "Not checked: the Microsoft account you signed in with has no Minecraft: Java "
            "Edition. Sign out and sign in with the one that has it."
        )
    return None


async def _join(ctx: ServerContext, run: Run, job: JobHandle, join: Join) -> None:
    host = server_ip(ctx)
    name = player_name(ctx, join)
    added = False
    if name:
        ctx.players.ignored.add(name)
    try:
        if not join.microsoft and _whitelist_on(ctx) and not _on_whitelist(ctx, TEST_PLAYER):
            added = True
            await ctx.player_actions.send("whitelist_add", TEST_PLAYER, "")
        job.step(f"{join.group} ({host}:{join.port})")
        stopped: list[str] = []

        def step(found: dict[str, Any]) -> None:
            why = _why(found)
            if str(found.get("error", "")).startswith("bedrock version"):
                ok = None  # the test player's limit, not the server's problem
            else:
                ok = found["ok"]
            run.line(join.group, STEP_WORDS[found["step"]], ok, why)
            if found["ok"] is False:
                stopped.append(found["step"])

        await joinbot.join(
            host,
            join.port,
            TEST_PLAYER if not join.microsoft else "owner",
            # Java: None makes the test player ask the server first. Bedrock
            # always asks (agent/joinbot.py has the versions it knows).
            ctx.server.mc_version if join.edition == "java" else None,
            f"Command Block test {secrets.token_hex(3)}",
            on_step=step,
            edition=join.edition,
            account=ctx.core.microsoft.account() if join.microsoft else None,
            on_account=ctx.core.microsoft.keep,
        )
        if stopped:
            at = joinbot.STEPS.index(stopped[-1])
            for later in joinbot.STEPS[at + 1 :]:
                run.line(join.group, STEP_WORDS[later], None, "Not tried: an earlier step failed.")
    except joinbot.BotError as exc:
        run.line(join.group, "Get the test player ready", False, str(exc))
    finally:
        if added:
            await ctx.player_actions.send("whitelist_remove", TEST_PLAYER, "")
        await asyncio.sleep(LEAVE_GRACE)
        if name:
            ctx.players.ignored.discard(name)


def _why(step: dict[str, Any]) -> str | None:
    if step.get("kick"):
        return f"The server said: {step['kick']}"
    error = str(step.get("error") or "")
    if error.startswith("bedrock version"):
        return (
            f"Not checked: this server runs Bedrock {error[len('bedrock version') :].strip()}. "
            "The test player only knows the newest Bedrock versions."
        )
    if error:
        return {
            "ECONNREFUSED": "Nothing answers on that port.",
            "ETIMEDOUT": "No answer from that address.",
            "Ping timed out": "No answer from that address.",
            "signed out": "Your Microsoft sign-in ran out. Sign in with Microsoft again.",
            "fetch failed": "Couldn't reach Microsoft's sign-in from this PC.",
        }.get(error, error)
    return None


async def run(out: Run, job: JobHandle, fixes: list | None = None) -> dict[str, Any]:
    """One round: make the picked ``fixes`` (agent/diagnosefix.py) if any,
    join, then put back what didn't help and offer what's new."""
    from . import diagnosefix, fullmods, waysin

    ctx = out.ctx
    try:
        if fixes and not await diagnosefix.make(out, fixes, job):
            return out.result
        planned = []
        for join in joins(ctx):
            why = _not_checked(ctx, join)
            if why:
                out.line(join.group, "Join", None, why)
                out.result["sign_in"] = out.result["sign_in"] or why == SIGN_IN_FIRST
            else:
                planned.append(join)
        if planned or out.result["mods"]:
            online = await _get_online(ctx, out, job)
            if online:
                await ctx.server.send_command(f"say {NOTICE}", internal=True)
                for join in planned:
                    await _join(ctx, out, job, join)
        else:
            online = ctx.server.state == ServerState.ONLINE
        if online:
            await waysin.check(out, job)
        if online and out.result["mods"]:
            await fullmods.check(out, job)
        await diagnosefix.review(out, fixes or [])
    finally:
        out.finish()
    return out.result
