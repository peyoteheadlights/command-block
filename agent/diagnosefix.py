"""The changes Diagnose server offers after a run, and putting back the ones
that didn't help.

After the joins, ``find()`` reads what went wrong (the run's lines, the
server's console and its newest crash) and lists the changes that may fix
it, each Safe or Risky, with what and why. The owner picks Apply all, Apply
safe or Deny all. The picked changes are made together through the
safe-change routine (one backup, one "Undo Diagnose changes"), then the
joins run again. A change whose problem is still there after that didn't
help, so it is put back. ``find()`` then looks again: a round that finds
nothing new ends it.

A change is only offered for a problem this run saw (house rule 1), and a
change already tried in this run is never offered again. Nothing here
deletes a world, a mod or a backup.

The ways in (agent/waysin.py) add Tailscale back on and the firewall rule;
the join with all mods (agent/fullmods.py) adds another version of the mod
the game won't start with, or turning it off.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import diagnose, firewall, fullmods, memory, tailscale, waysin
from .minecraft.playeractions import REASON_RE, read_list
from .minecraft.state import ServerState
from .safechange import SafeChange, SafeChangeError, run_safe_change
from .servertypes.create import EULA_URL, write_eula

if TYPE_CHECKING:
    from .core import ServerContext
    from .jobs import JobHandle

TITLE = "Diagnose changes"
# Java "You are not white-listed on this server!", Bedrock "You are not
# invited to this server" / disconnectionScreen.notAllowed.
NOT_LISTED = re.compile(r"white-?listed|not invited|notAllowed|allowlist", re.IGNORECASE)
BANNED = re.compile(r"\bbanned\b", re.IGNORECASE)
NO_ANSWER = ("Nothing answers on that port.", "No answer from that address.")


@dataclass
class Fix:
    """One change Diagnose can offer. ``group`` names the result lines that
    show whether it helped."""

    key: str
    title: str
    why: str
    safe: bool
    group: str
    do: Callable[[], Awaitable[Any]]
    # Puts it back when it didn't help. None: nothing to put back (a restart).
    undo: Callable[[], Awaitable[Any]] | None = None
    # Files in the server's folder it changes, backed up before.
    files: tuple[str, ...] = ()

    def view(self) -> dict[str, Any]:
        return {"key": self.key, "title": self.title, "why": self.why, "safe": self.safe}


def find(ctx: ServerContext, result: dict[str, Any]) -> list[Fix]:
    """The changes for what this run saw go wrong, safe ones first."""
    found: dict[str, Fix] = {}
    failed = [line for line in result["lines"] if line["ok"] is False]
    if any(line["group"] == diagnose.SERVER_GROUP for line in failed):
        if ctx.server.eula_required or ctx.server.eula_not_accepted():
            found["eula"] = _eula(ctx)
        more = _more_memory(ctx, result["started_at"])
        if more:
            found["memory"] = more
    joins = {join.group: join for join in diagnose.joins(ctx)}
    for line in failed:
        join = joins.get(line["group"])
        detail = line.get("detail") or ""
        if join is None:
            continue
        if (
            line["what"] == diagnose.STEP_WORDS["connect"]
            and detail in NO_ANSWER
            and ctx.server.state == ServerState.ONLINE
        ):
            found["restart"] = _restart(ctx, join.group)
        name = diagnose.player_name(ctx, join) if join.microsoft else None
        if name and NOT_LISTED.search(detail):
            found[f"whitelist {name}"] = _whitelist(ctx, name, join.group)
        elif name and BANNED.search(detail) and ctx.config.server_type.dialect == "java":
            found[f"unban {name}"] = _unban(ctx, name, join.group)
    for line in failed:
        if line["group"] == waysin.TAILSCALE and line["detail"] == waysin.SWITCHED_OFF:
            found["tailscale"] = _tailscale()
    found.update(_firewall(ctx, result["lines"], result["tried"]))
    for line in fullmods.broken_lines(result):
        fix = _mod(ctx, line, result["by"])
        if fix:
            found[fix.key] = fix
    return sorted(found.values(), key=lambda fix: not fix.safe)


# ------------------------------------------------------------- the changes
def _restart(ctx: ServerContext, group: str) -> Fix:
    async def do() -> None:
        await ctx.server.restart(actor=f"Diagnose server ({TITLE})")

    return Fix(
        "restart",
        "Restart the server",
        "It says Online, but nothing answers on its game port from this PC. "
        "A restart often gets it answering again.",
        True,
        group,
        do,
    )


def _more_memory(ctx: ServerContext, since: float) -> Fix | None:
    """1 GB more, only when the start just ran out of memory and the PC has
    room for it next to the other running servers."""
    newest = ctx.crashes.list_crashes(1)
    if not newest or newest[0]["ts"] < since:
        return None
    crash = newest[0]
    if crash["category"] != "OutOfMemoryError" or crash["confidence"] not in (
        "confirmed",
        "likely",
    ):
        return None
    limit = memory.memory_limit_mb(ctx.config.server.jvm_args)
    total = memory.total_ram_mb()
    if not ctx.config.server_type.takes_memory_limit or not limit or not total:
        return None  # no limit set or the PC's memory unknown: nothing to add to
    together, _rows = memory.together_mb(ctx)
    more = limit + 1024
    if together - limit + more > total:
        return None
    before = list(ctx.config.server.jvm_args)

    async def put(args: list[str]) -> None:
        old = list(ctx.config.server.jvm_args)
        ctx.config.set("server.jvm_args", args)
        try:
            ctx.config.save()
        except OSError:
            ctx.config.set("server.jvm_args", old)
            raise

    return Fix(
        "memory",
        f"Give the server more memory ({limit / 1024:g} GB to {more / 1024:g} GB)",
        "It ran out of memory while starting. This PC has room for 1 GB more. "
        "Memory is a setting: Undo doesn't change it back, the memory slider in "
        "Game settings does.",
        True,
        diagnose.SERVER_GROUP,
        lambda: put(memory.with_limit(before, more)),
        lambda: put(before),
    )


def _tailscale() -> Fix:
    async def switch(on: bool) -> None:
        await asyncio.to_thread(tailscale.switch, on)

    return Fix(
        "tailscale",
        "Turn Tailscale back on",
        waysin.SWITCHED_OFF,
        True,
        waysin.TAILSCALE,
        lambda: switch(True),
        lambda: switch(False),
    )


def _firewall(ctx: ServerContext, lines: list[dict[str, Any]], tried: list[str]) -> dict[str, Fix]:
    """Let a port through Windows Firewall, when it answers on this PC but
    not from outside (the other PC or the internet; playit comes in through
    its own program, so not playit). A rule of ours that is there already
    isn't offered, unless this run made it (then it's being checked)."""
    here = {(x["protocol"], x["port"]) for x in lines if x.get("outside") is False and x["ok"]}
    found: dict[str, Fix] = {}
    if not firewall.IS_WINDOWS:
        return found
    for line in lines:
        where = (line.get("protocol"), line.get("port"))
        if (
            not line.get("outside")
            or line["ok"] is not False
            or line["detail"] != waysin.NO_ANSWER
            or line["group"] == waysin.PLAYIT
            or where not in here
        ):
            continue
        protocol = "udp" if where[0] == "raknet" else "tcp"
        port = int(line["port"])
        key = f"firewall {protocol} {port}"
        if key in found:
            continue
        name = firewall.rule_name(ctx.server_id, protocol, port)
        if key not in tried and firewall.has_rule(name):
            continue
        found[key] = _rule(key, name, protocol, port, line["group"])
    return found


def _rule(key: str, name: str, protocol: str, port: int, group: str) -> Fix:
    async def do() -> None:
        await asyncio.to_thread(firewall.add, name, protocol, port)

    async def undo() -> None:
        await asyncio.to_thread(firewall.remove, name)

    return Fix(
        key,
        f"Let port {port} ({protocol.upper()}) through Windows Firewall",
        "The server answers on this PC but not from outside it, so the Windows Firewall "
        "may be blocking it. Windows asks for permission first. Undo doesn't take the "
        "rule away; Diagnose does when it didn't help.",
        False,
        group,
        do,
        undo,
    )


def _mod(ctx: ServerContext, line: dict[str, Any], by: str) -> Fix | None:
    """The other version the game started with (safe), or turning the mod
    off (risky). Both need the server stopped; it is started again after."""
    from .mods.jarinfo import DISABLED_SUFFIX

    try:
        mod = ctx.mods.find(line["broken"])
    except Exception:
        return None  # changed by hand since: nothing to offer
    name = line.get("name") or mod.mod_id
    user = f"Diagnose server ({by})"
    if line.get("version_id"):
        kept: dict[str, Any] = {}

        async def update() -> None:
            done = await ctx.mods.update(mod.filename, user, line["version_id"])
            kept.update(done)

        async def back() -> None:
            await ctx.mods.rollback(mod.mod_id, kept["rollback_archive"], user)

        return Fix(
            f"mod {mod.mod_id}",
            f"Change {name} to version {line['version']}",
            f"The game won't start with {name} {mod.version}, but it does with "
            f"{line['version']}. Players need the same version.",
            True,
            fullmods.GROUP,
            lambda: _stopped(ctx, update),
            lambda: _stopped(ctx, back),
            ("mods",),
        )

    async def turn(on: bool) -> None:
        filename = mod.filename if not on else mod.filename + DISABLED_SUFFIX
        await ctx.mods.set_enabled(filename, on, user)

    return Fix(
        f"mod {mod.mod_id}",
        f"Turn off {name}",
        f"The game won't start with {name}, and no other version of it helped. "
        "Turning it off takes it out of the game for everyone; it isn't deleted.",
        False,
        fullmods.GROUP,
        lambda: _stopped(ctx, lambda: turn(False)),
        lambda: _stopped(ctx, lambda: turn(True)),
        ("mods",),
    )


async def _stopped(ctx: ServerContext, change: Callable[[], Awaitable[Any]]) -> None:
    """Mods change only while the server is stopped; it starts again after."""
    running = ctx.server.running
    if running:
        await ctx.server.stop(actor=f"Diagnose server ({TITLE})")
    try:
        await change()
    finally:
        if running:
            await ctx.server.start(actor=f"Diagnose server ({TITLE})")


def _eula(ctx: ServerContext) -> Fix:
    folder = ctx.config.server_dir

    async def do() -> None:
        write_eula(folder, True)
        ctx.server.eula_required = False

    async def undo() -> None:
        write_eula(folder, False)

    return Fix(
        "eula",
        "Accept the Minecraft EULA",
        "The server won't start until its owner agrees to Minecraft's rules "
        f"({EULA_URL}). Applying this means you agree to them.",
        False,
        diagnose.SERVER_GROUP,
        do,
        undo,
        ("eula.txt",),
    )


def _whitelist(ctx: ServerContext, name: str, group: str) -> Fix:
    async def do() -> None:
        await ctx.player_actions.send("whitelist_add", name)

    async def undo() -> None:
        await ctx.player_actions.send("whitelist_remove", name)

    return Fix(
        f"whitelist {name}",
        f"Add {name} to the whitelist",
        f"The server turned your account ({name}) away because it isn't on the whitelist.",
        False,
        group,
        do,
        undo,
        ("whitelist.json", "allowlist.json"),
    )


def _unban(ctx: ServerContext, name: str, group: str) -> Fix:
    banned = read_list(ctx.config.server_dir, "banned-players")["players"] or []
    entry: dict[str, Any] = next(
        (p for p in banned if p.get("name", "").lower() == name.lower()), {}
    )
    reason = entry.get("reason") or ""
    reason = reason if REASON_RE.match(reason) else ""

    async def do() -> None:
        await ctx.player_actions.send("pardon", name)

    async def undo() -> None:
        await ctx.player_actions.send("ban", name, reason)

    return Fix(
        f"unban {name}",
        f"Unban {name}",
        f"The server turned your account ({name}) away because it is banned there.",
        False,
        group,
        do,
        undo,
        ("banned-players.json",),
    )


# ------------------------------------------------------------- one round
def offer(ctx: ServerContext, result: dict[str, Any]) -> None:
    """Put the changes this run found, minus the ones tried, on the result."""
    result["changes"] = [fix.view() for fix in find(ctx, result) if fix.key not in result["tried"]]


def picked(ctx: ServerContext, result: dict[str, Any], pick: str) -> list[Fix]:
    """The offered changes the owner picked ("all" or "safe"), found again
    now so a problem fixed by hand since isn't changed twice."""
    keys = {c["key"] for c in result.get("changes") or [] if pick == "all" or c["safe"]}
    return [fix for fix in find(ctx, result) if fix.key in keys]


def deny(result: dict[str, Any]) -> None:
    for change in result.get("changes") or []:
        result["done"].append(_done(change["title"], change["safe"], "denied", "Not made."))
        result["tried"].append(change["key"])
    result["changes"] = []


async def make(out: diagnose.Run, fixes: list[Fix], job: JobHandle) -> bool:
    """Make the picked changes together, with one backup and one Undo.
    False when they couldn't be made (and nothing was left half done)."""
    ctx = out.ctx
    folder = ctx.config.server_dir
    files = sorted({f for fix in fixes for f in fix.files if (folder / f).exists()})

    async def change(handle: JobHandle | None) -> dict[str, Any]:
        # A restart last: the other changes take effect with it.
        for fix in sorted(fixes, key=lambda fix: fix.key == "restart"):
            if handle:
                handle.step(fix.title)
            await fix.do()
        return {}

    plan = SafeChange(
        title=TITLE,
        change=change,
        stop_server=False,
        take_backup=bool(files),
        backup_name="pre-diagnose",
        backup_includes=files,
        undo_scope=files,
    )
    out.result["tried"].extend(fix.key for fix in fixes)
    try:
        await run_safe_change(ctx.server, ctx.backups, plan, job=job, user=out.result["by"])
    except SafeChangeError as exc:
        for fix in fixes:
            out.result["done"].append(_done(fix.title, fix.safe, "failed", str(exc)))
        return False
    return True


async def review(out: diagnose.Run, fixes: list[Fix]) -> None:
    """After the joins ran again: keep what helped, put back what didn't,
    then offer what's new."""
    ctx = out.ctx
    again = {fix.key for fix in find(ctx, out.result)}
    for fix in fixes:
        lines = [line for line in out.result["lines"] if line["group"] == fix.group]
        if fix.key in again:
            outcome, detail = "put_back", "It didn't help, so it was put back."
            if fix.undo is None:
                outcome, detail = "no_help", "It didn't help."
            else:
                try:
                    await fix.undo()
                except Exception as exc:
                    outcome = "failed"
                    detail = f"It didn't help, and putting it back failed: {exc}"
        elif not any(line["ok"] is not None for line in lines):
            outcome, detail = "kept", "Kept. Not checked again: an earlier step failed."
        else:
            outcome, detail = "kept", "Kept: that problem is gone."
        out.result["done"].append(_done(fix.title, fix.safe, outcome, detail))
    offer(ctx, out.result)


def _done(title: str, safe: bool, outcome: str, detail: str) -> dict[str, Any]:
    return {"title": title, "safe": safe, "outcome": outcome, "detail": detail}
