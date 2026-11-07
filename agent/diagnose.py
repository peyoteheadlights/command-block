"""Diagnose server: join the server like a player would and say what happened.

Runs only when someone clicks "Diagnose server" (weak PCs first), as a job.
A stopped server is started (the click is the yes). Then the test player
(agent/joinbot.py) joins from this PC and every step it gets through, or the
step it stopped at and why, becomes a line in the result. The result never
says "healthy": it lists what was tried and what happened.

So far one join: from this PC, on an offline-mode Java server, as the
``CommandBlock`` test player. Online-mode and Bedrock joins need the owner's
Microsoft account (next part), and the other ways in (LAN, Tailscale, the
internet, playit) and the list of fixes come after that.

The last result is kept on the server (``ctx.diagnosis``) and updated while
it runs, so the apps can show the list live.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import TYPE_CHECKING, Any

from . import joinbot
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


class Run:
    """One Diagnose run's list, shared live through ``ctx.diagnosis``."""

    def __init__(self, ctx: ServerContext, by: str):
        self.ctx = ctx
        self.result: dict[str, Any] = {
            "running": True,
            "started_at": time.time(),
            "finished_at": None,
            "by": by,
            "lines": [],
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
    group = "The server"
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
    if not await server.wait_online(timeout=START_WAIT):
        run.line(
            group,
            "Start the server",
            False,
            "It didn't finish starting. The console and the crash report say why.",
        )
        return False
    run.line(group, "Start the server", True, started)
    return True


async def _join_from_this_pc(ctx: ServerContext, run: Run, job: JobHandle) -> None:
    group = f"Join from this PC as {TEST_PLAYER}"
    port = ctx.core.ports.port_of(ctx).port
    host = server_ip(ctx)
    added = False
    ctx.players.ignored.add(TEST_PLAYER)
    try:
        if _whitelist_on(ctx) and not _on_whitelist(ctx, TEST_PLAYER):
            added = True
            await ctx.player_actions.send("whitelist_add", TEST_PLAYER, "")
        await ctx.server.send_command(f"say {NOTICE}", internal=True)
        job.step(f"Joining from this PC ({host}:{port})")
        chat = f"Command Block test {secrets.token_hex(3)}"
        steps = await joinbot.join(
            host,
            port,
            TEST_PLAYER,
            ctx.server.mc_version,  # None: the test player asks the server first
            chat,
            on_step=lambda s: run.line(group, STEP_WORDS[s["step"]], s["ok"], _why(s)),
        )
        if steps and steps[-1]["ok"] is False:
            stopped = joinbot.STEPS.index(steps[-1]["step"])
            for step in joinbot.STEPS[stopped + 1 :]:
                run.line(group, STEP_WORDS[step], None, "Not tried: an earlier step failed.")
    except joinbot.BotError as exc:
        run.line(group, "Get the test player ready", False, str(exc))
    finally:
        if added:
            await ctx.player_actions.send("whitelist_remove", TEST_PLAYER, "")
        await asyncio.sleep(LEAVE_GRACE)
        ctx.players.ignored.discard(TEST_PLAYER)


def _why(step: dict[str, Any]) -> str | None:
    if step.get("kick"):
        return f"The server said: {step['kick']}"
    if step.get("error"):
        return {
            "ECONNREFUSED": "Nothing answers on that port.",
            "ETIMEDOUT": "No answer from that address.",
        }.get(step["error"], step["error"])
    return None


async def run(out: Run, job: JobHandle) -> dict[str, Any]:
    ctx = out.ctx
    try:
        edition = ctx.config.server_type.edition
        if edition == "bedrock":
            out.line(
                "Joins",
                "Join as a Bedrock player",
                None,
                "Not checked yet: Bedrock joins need your Xbox account.",
            )
        elif online_mode(ctx) is not False:
            out.line(
                "Joins",
                "Join with your Minecraft account",
                None,
                "Not checked yet: online-mode servers need your Microsoft account.",
            )
        elif await _get_online(ctx, out, job):
            await _join_from_this_pc(ctx, out, job)
    finally:
        out.finish()
    return out.result
