"""Less downtime: what a server is waiting to restart for, "Restart when
nobody's on", and stopping and starting every server together when the
agent itself stops and starts.

The agent used to leave Minecraft running when it stopped, then forgot it
(audit: "When the agent restarts, it forgets a server that is still
running"). Now it warns the players, stops every server at once (each one
saves its world first, up to its own "Force stop after" time), writes down
which were running, and starts those again together when it starts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .events import Event
from .minecraft.process import ServerError
from .minecraft.state import ExitReason, ServerState

if TYPE_CHECKING:
    from .core import AgentCore, ServerContext

log = logging.getLogger(__name__)

# Players get this long between the warning and the stop.
WARN_SECONDS = 10.0
# How often "Restart when nobody's on" looks at the player count.
EMPTY_CHECK_SECONDS = 15.0
# The servers that were running when the agent stopped (a list of ids).
RUNNING_KEY = "running_when_agent_stopped"


def waiting(ctx: ServerContext) -> list[str]:
    """What changed since the running server started that it only reads
    at start: "settings" (server.properties), "memory" (memory, Java or its
    arguments), and the marks other changes leave ("mods", "files",
    "addons", "join", "icon"). Empty when it's stopped: a start uses it all."""
    from .gamesettings import waiting_for_restart

    server = ctx.server
    if not server.running:
        return []
    found = set(server.waiting)
    if waiting_for_restart(ctx):
        found.add("settings")
    if server.command_changed():
        found.add("memory")
    listener = ctx.core.guard.listeners.get(ctx.server_id)
    if bool(ctx.config.server.join_protection) != bool(listener and listener.mode == "proxy"):
        found.add("join")
    return sorted(found)


def mark(ctx: ServerContext, what: str) -> None:
    """A change Minecraft only reads at start was made: remember it while
    the server runs."""
    if ctx.server.running:
        ctx.server.waiting.add(what)


# -- Restart when nobody's on ----------------------------------------------
async def restart_when_empty(ctx: ServerContext, user: str) -> dict[str, Any]:
    """Restart at the next moment nobody is playing, so no player is kicked.
    An unknown player count is not "nobody": the agent asks with /list."""
    server = ctx.server
    if server.state != ServerState.ONLINE:
        raise ServerError("The server isn't running, so the changes are used at its next start.")
    server.restart_when_empty = True
    await ctx.bus.publish(
        Event(
            type="restart_when_empty",
            level="info",
            message="The server restarts as soon as nobody is playing.",
            data={"user": user},
        )
    )
    task = ctx._empty_task
    if task is None or task.done():
        ctx._empty_task = asyncio.create_task(_watch_empty(ctx), name="restart-when-empty")
    return {"restart_when_empty": True}


async def cancel_restart_when_empty(ctx: ServerContext, user: str) -> dict[str, Any]:
    ctx.server.restart_when_empty = False
    task = ctx._empty_task
    if task is not None and not task.done():
        task.cancel()
    await ctx.bus.publish(
        Event(
            type="restart_when_empty_cancelled",
            level="info",
            message="The restart when nobody's on was cancelled.",
            data={"user": user},
        )
    )
    return {"restart_when_empty": False}


async def _watch_empty(ctx: ServerContext) -> None:
    server = ctx.server
    while server.restart_when_empty and server.state == ServerState.ONLINE:
        if await restart_if_empty(ctx):
            return
        await asyncio.sleep(EMPTY_CHECK_SECONDS)
    # Stopped meanwhile: the next start uses the changes, nothing is left to do.
    server.restart_when_empty = False


async def restart_if_empty(ctx: ServerContext) -> bool:
    """Restart now when the server is online and verified empty."""
    server = ctx.server
    if not server.restart_when_empty or server.state != ServerState.ONLINE:
        return False
    count = ctx.players.online_count()
    if count is None:
        # Not known yet: ask, and look again next time.
        await server.send_command("list", internal=True)
        return False
    if count:
        return False
    server.restart_when_empty = False
    await server.restart(actor="restart when nobody's on")
    return True


# -- the agent stopping and starting ---------------------------------------
async def stop_for_agent(core: AgentCore) -> list[str]:
    """Warn players, stop every running server at once and wait for each to
    save, and write down which were running. Returns their ids."""
    # One left running by an agent that died has no console to stop it with.
    running = [
        ctx
        for ctx in core.servers.values()
        if ctx.server.running and ctx.server.left_behind is None
    ]
    ids = [ctx.server_id for ctx in running]
    core.db.set_setting(RUNNING_KEY, ids)
    if not running:
        return ids
    # Unknown (None) counts as maybe someone: warn rather than guess.
    told = [ctx for ctx in running if ctx.players.online_count() != 0]
    for ctx in told:
        try:
            await ctx.server.send_command(
                f"say This server restarts in {WARN_SECONDS:.0f} seconds. "
                "It's back in a minute or two.",
                internal=True,
            )
        except Exception:  # a warning that can't be sent never blocks the stop
            log.debug("could not warn players on %s", ctx.server_id, exc_info=True)
    if told:
        await asyncio.sleep(WARN_SECONDS)

    async def stop(ctx: ServerContext) -> None:
        try:
            await ctx.server.stop(actor="agent stopping", reason=ExitReason.USER_STOP)
        except ServerError:
            pass  # it stopped by itself meanwhile
        except Exception:
            log.exception("could not stop %s while the agent stopped", ctx.server_id)

    await asyncio.gather(*(stop(ctx) for ctx in running))
    return ids


async def start_after_agent(core: AgentCore) -> None:
    """Start, all together, the servers that were running when the agent
    stopped and the ones set to start with it."""
    remembered = core.db.get_setting(RUNNING_KEY) or []
    core.db.set_setting(RUNNING_KEY, [])
    wanted = [
        ctx
        for ctx in core.servers.values()
        if (ctx.server_id in remembered or ctx.config.server.autostart_minecraft)
        and not ctx.server.running  # never started twice
    ]

    async def start(ctx: ServerContext) -> None:
        try:
            await ctx.server.start(actor="agent-autostart")
        except ServerError as exc:
            await ctx.bus.publish(
                Event(
                    type="autostart_failed",
                    level="error",
                    message=f"Automatic start failed: {exc}",
                )
            )

    await asyncio.gather(*(start(ctx) for ctx in wanted))
