"""Newer server software: once a day, ask each server's official source
whether there is something newer than what the console last printed.

Two things are looked for on a Java server, each its own Recommendation
(agent/recommendations.py) whose button runs the normal version change
(backup first, one Undo):

  * a newer stable build of the same Minecraft version ("Paper build 512")
  * a newer stable Minecraft release for the same server type

Snapshots and unstable builds are never offered. Nothing comes from a
saved list (agent/savedlists.py): when a site could only be read from its
saved copy, that day's check of it is skipped. Each newly offered version
sends one low-priority alert, never one a day.

Bedrock: with "Update Bedrock by itself" on (off by default, owner only),
a newer Bedrock server is put in once nobody is online, through the same
version change. Bedrock players' games update themselves and then can't
join an older server.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from . import savedlists
from .downloads import DownloadError
from .events import Event

if TYPE_CHECKING:
    from .core import AgentCore, ServerContext

log = logging.getLogger("msc.softwarecheck")

CHECK_EVERY = 24 * 3600
# After the update check (15 minutes), so starting stays quick.
FIRST_CHECK_DELAY = 20 * 60
# Each server a little after the one before, so a weak PC never asks every
# site at once.
STAGGER = 30
# Bedrock: how often to look again while players are online.
WAIT_FOR_EMPTY = 5 * 60
OFFERS_KEY = "software_offers"
ALERTED_KEY = "software_alerted"
RELEASE_RE = re.compile(r"^\d+(\.\d+)+$")
# Types whose console prints "<minecraft>-<build>-..." and whose source lists
# builds by number. Types from the notes file are Paper's.
BUILD_NUMBERED = ("paper", "purpur", "leaf")


def numbered(type_id: str) -> bool:
    from . import servertypes

    found = servertypes.TYPES.get(type_id)
    return type_id in BUILD_NUMBERED or bool(found and found.like == "paper")


def build_of(type_id: str, loader: str | None) -> str | None:
    """The build number in what the console printed, in the form the
    source lists it: Paper "1.21.1-129-main@abc" is build "129". None when
    the console didn't print a number there (a build made by hand)."""
    if not loader:
        return None
    if numbered(type_id):
        parts = loader.split("-")
        return parts[1] if len(parts) > 1 and parts[1].isdigit() else None
    return loader


def source_build(type_id: str, loader: str | None) -> str | None:
    """Forge lists builds as "<minecraft>-<build>"; the console prints only
    the build."""
    if loader and type_id == "forge":
        return loader.partition("-")[2] or None
    return loader


class SoftwareCheck:
    def __init__(self, core: AgentCore):
        self.core = core
        self._task: asyncio.Task | None = None
        # Bedrock updates waiting for nobody to be online, by server id.
        self._waiting: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------ Java servers
    async def check_server(self, ctx: ServerContext) -> dict[str, Any]:
        """Look for newer software for one server. Returns what is offered
        now, as stored for the Recommendations."""
        from .servertypes.versions import list_versions, make_plan, version_key

        server_type = ctx.config.server_type
        status = ctx.server.status()
        minecraft = status.get("minecraft_version")
        installed = build_of(server_type.id, status.get("loader_version"))
        if ctx.config.server.custom_jar:
            return {}  # never offered an update: the app doesn't know where it came from
        offers = dict(ctx.db.get_setting(OFFERS_KEY) or {})
        if not minecraft:
            return offers  # nothing printed yet: no guess
        if server_type.picks_loader or numbered(server_type.id):
            try:
                with savedlists.watch() as used:
                    plan = await make_plan(server_type.id, minecraft)
                if not used:
                    newest = source_build(server_type.id, plan.loader)
                    newer = bool(
                        newest
                        and installed
                        and plan.stable
                        and version_key(newest) > version_key(installed)
                    )
                    offers["build"] = (
                        {
                            "minecraft": minecraft,
                            "installed": installed,
                            "build": newest,
                            "loader": plan.loader,
                        }
                        if newer
                        else None
                    )
            except DownloadError as exc:
                log.info("couldn't check %s's builds: %s", ctx.name, exc)
        try:
            with savedlists.watch() as used:
                versions = await list_versions(server_type.id)
            if not used:
                newest_release = next(
                    (v.minecraft for v in versions if v.stable and RELEASE_RE.match(v.minecraft)),
                    None,
                )
                newer = bool(
                    newest_release
                    and RELEASE_RE.match(minecraft)
                    and version_key(newest_release) > version_key(minecraft)
                )
                offers["minecraft"] = (
                    {"from": minecraft, "minecraft": newest_release} if newer else None
                )
        except DownloadError as exc:
            log.info("couldn't check %s's versions: %s", ctx.name, exc)
        ctx.db.set_setting(OFFERS_KEY, offers)
        await self._alert(ctx, offers)
        return offers

    async def _alert(self, ctx: ServerContext, offers: dict[str, Any]) -> None:
        """One low-priority alert per newly offered version."""
        name = ctx.config.server_type.name
        alerted = list(ctx.db.get_setting(ALERTED_KEY) or [])
        for kind, offer in offers.items():
            if not offer:
                continue
            key = f"{kind}:{offer['minecraft']}:{offer.get('build') or ''}"
            if key in alerted:
                continue
            alerted.append(key)
            message = (
                f"{name} build {offer['build']} is out for Minecraft {offer['minecraft']}"
                if kind == "build"
                else f"Minecraft {offer['minecraft']} is out for {name}"
            )
            await ctx.bus.publish(
                Event(
                    type="server_software_available",
                    level="info",
                    message=message,
                    data={"kind": kind, **offer},
                )
            )
        ctx.db.set_setting(ALERTED_KEY, alerted[-50:])

    # ------------------------------------------------------------ Bedrock
    async def update_bedrock(self, ctx: ServerContext) -> dict[str, Any] | None:
        """With the switch on: put the newest Bedrock server in once nobody
        is online. Returns the change's result, or None when nothing was done."""
        from .servertypes import bedrock
        from .servertypes.install import change_version
        from .servertypes.versions import version_key

        if not ctx.config.server.bedrock_auto_update:
            return None
        installed = ctx.server.status().get("minecraft_version")
        try:
            newest, _url = await bedrock.latest()
        except DownloadError as exc:
            log.info("couldn't check for a newer Bedrock server: %s", exc)
            return None
        if not installed or version_key(newest) <= version_key(installed):
            return None
        await self._alert(ctx, {"minecraft": {"from": installed, "minecraft": newest}})
        # Waits for a verified zero: an unknown count is not "nobody".
        while ctx.server.running and ctx.players.online_count() != 0:
            await asyncio.sleep(WAIT_FOR_EMPTY)
            if not ctx.config.server.bedrock_auto_update:
                return None
        return await change_version(
            ctx, "bedrock", newest, user="agent", start_after=ctx.server.running
        )

    # ------------------------------------------------------------ the loop
    async def check_all(self) -> None:
        for i, ctx in enumerate(list(self.core.servers.values())):
            if i:
                await asyncio.sleep(STAGGER)
            try:
                if ctx.config.server_type.needs_java:
                    await self.check_server(ctx)
                else:
                    waiting = self._waiting.get(ctx.server_id)
                    if waiting is None or waiting.done():
                        # Its own task: it may wait hours for players to leave.
                        self._waiting[ctx.server_id] = asyncio.create_task(
                            self.update_bedrock(ctx), name=f"bedrock-update-{ctx.server_id}"
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("checking %s for newer software failed", ctx.name, exc_info=True)

    async def _loop(self) -> None:
        await asyncio.sleep(FIRST_CHECK_DELAY)
        while True:
            await self.check_all()
            await asyncio.sleep(CHECK_EVERY)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="software-check")

    async def stop(self) -> None:
        for task in (self._task, *self._waiting.values()):
            if task:
                task.cancel()
