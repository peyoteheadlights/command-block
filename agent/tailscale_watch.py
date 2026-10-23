"""The agent's half of the desktop app's Tailscale step.

The desktop app installs Tailscale and signs it in. Then it asks the agent to
move onto the Tailscale address. The agent only moves once Tailscale itself
says it is connected (house rule 1), and it keeps checking every minute, so
the move finishes even if the app was closed half way.

After the move, the same minute check notices when Tailscale stops, which
means phones can't reach this PC any more, and tells the owner once.

Tailscale is only asked while the step is running or after the move, so a
PC that skipped the step runs no command every minute (house rule 7).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from typing import Any

from . import tailscale_share
from .events import Event
from .tailscale import tailscale_status

log = logging.getLogger("msc.tailscale")

STATE_KEY = "tailscale"
CHECK_EVERY = 60
# Tailscale gives each PC an address in this range; any other address was
# set by hand (a LAN address, say) and is left alone.
TAILSCALE_RANGE = ipaddress.ip_network("100.64.0.0/10")
# A move the person walked away from (a sign-in never finished) stops
# counting after this, so "Tailscale stopped" isn't silenced for good.
MOVING_FOR = 30 * 60


class MoveRefused(RuntimeError):
    """Said to the owner as it is."""


class TailscaleWatch:
    def __init__(self, core) -> None:
        self.core = core
        self.down = False  # Tailscale said it stopped after the move
        self.restarting = False  # moved; the agent is about to restart
        self._task: asyncio.Task | None = None

    def saved(self) -> dict[str, Any]:
        return self.core.db.get_setting(STATE_KEY, {}) or {}

    def _save(self, **changes: Any) -> None:
        self.core.db.set_setting(STATE_KEY, {**self.saved(), **changes})

    @property
    def moved(self) -> bool:
        try:
            return ipaddress.ip_address(self.core.config.network.host) in TAILSCALE_RANGE
        except ValueError:
            return False  # a name, or nothing: not an address Tailscale gave

    @property
    def moving(self) -> bool:
        started = self.saved().get("moving") or 0
        return bool(started) and time.time() - float(started) < MOVING_FOR

    def state(self, report: dict[str, Any]) -> dict[str, Any]:
        saved = self.saved()
        return {
            "moving": self.moving,
            "skipped": bool(saved.get("skipped")),
            "moved": self.moved,
            "restarting": self.restarting,
            "address": self.core.config.network.host if self.moved else None,
            "installed": report.get("cli_found"),
            "connected": report.get("connected"),
            "account": report.get("account"),
            "error": report.get("error"),
        }

    async def status(self) -> dict[str, Any]:
        return self.state(await asyncio.to_thread(tailscale_status))

    async def start_move(self) -> dict[str, Any]:
        if not self.core.config.tls_enabled:
            # main.py refuses plain HTTP on any address but 127.0.0.1.
            raise MoveRefused(
                "HTTPS is off on this PC, so the agent can't listen on Tailscale. "
                "Run the installer again to turn it back on."
            )
        self._save(moving=time.time())
        report = await asyncio.to_thread(tailscale_status)
        await self.check(report)
        return self.state(report)

    def skip(self) -> dict[str, Any]:
        self._save(moving=False, skipped=True)
        return self.state({})

    async def tick(self) -> None:
        if not (self.moving or self.moved):
            return
        await self.check(await asyncio.to_thread(tailscale_status))

    async def check(self, report: dict[str, Any]) -> None:
        connected = report.get("connected")
        if connected is True:
            self.down = False
            ipv4 = next((a for a in report.get("addresses") or [] if ":" not in a), None)
            # A different account gives the PC a new address: move again.
            if ipv4 and ipv4 != self.core.config.network.host:
                await self._move(ipv4)
            elif ipv4 and self.moving:
                self._save(moving=False)  # already there (the same account again)
        elif connected is False and self.moved and not self.down:
            # None means Tailscale couldn't be asked, which proves nothing.
            if self.moving:
                return  # signed out on purpose to use a different account
            self.down = True
            await self.core.bus.publish(
                Event(
                    type="tailscale_stopped",
                    level="error",
                    message="Phones can't reach this PC: Tailscale stopped",
                    data={"backend_state": report.get("backend_state")},
                )
            )

    async def _move(self, address: str) -> None:
        log.info("Tailscale is connected. Moving the agent onto %s.", address)
        self.core.config.set("network.host", address)
        await asyncio.to_thread(self.core.config.save)
        self._save(moving=False)
        await self.core.bus.publish(
            Event(
                type="phones_can_reach",
                level="success",
                message="Phones can now reach this PC",
                data={"address": address},
            )
        )
        # The agent stops and the startup task starts it again on the new address.
        self.restarting = True
        self.core.request_restart()

    async def remind_key(self) -> None:
        """Once, about a week before a saved API token runs out."""
        found = tailscale_share.state(self.core.config)
        if not found["expiring"] or self.saved().get("key_warned") == found["expires_at"]:
            return
        self._save(key_warned=found["expires_at"])
        await self.core.bus.publish(
            Event(
                type="tailscale_key_expiring",
                level="warn",
                message="The Tailscale key that makes share links runs out within a week. "
                "Make a new one and save it in Moderators.",
                data={"expires_at": found["expires_at"]},
            )
        )

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(CHECK_EVERY)
            try:
                await self.tick()
                await self.remind_key()
            except Exception:
                log.exception("Tailscale check failed")

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="tailscale-watch")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
