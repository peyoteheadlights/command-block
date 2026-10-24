"""The getting-started checklist on a new server's Overview.

A few things worth doing once, each ticked off from something the app
measured rather than from a click on the item (house rule 1): the server is
set up, a backup has really run and checked out, somebody has really
joined, alerts are really switched on, if the desktop app's Tailscale
step was skipped, the agent really listens on Tailscale and, once a Discord
bot token is set, the bot is connected and set up without problems. Pressing an item only takes you
to the page where it is done.

The card can be dismissed, which is remembered per server. Once every item
is done it is finished and does not come back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import ServerContext

STATE_KEY = "getting_started"


def _server_ready(ctx: ServerContext) -> dict[str, Any]:
    """Done once this server could actually be launched: its folder is
    there and its software (a jar, or Forge's start file) is in place."""
    problem = ctx.server.launch_problem()
    return {
        "id": "server_ready",
        "done": problem is None,
        "page": "settings",
        "evidence_key": "ready" if problem is None else "not_ready",
        "evidence": {"folder": str(ctx.config.server_dir), "problem": problem},
    }


def _backup_taken(ctx: ServerContext) -> dict[str, Any]:
    good = [b for b in ctx.backups.list_backups() if b.get("status") == "ok"]
    newest = good[0] if good else None
    return {
        "id": "backup_taken",
        "done": bool(newest),
        "page": "backups",
        "evidence_key": "backup_done" if newest else "backup_none",
        "evidence": {
            "name": newest["name"] if newest else None,
            "created_at": newest["created_at"] if newest else None,
        },
    }


def _friend_joined(ctx: ServerContext) -> dict[str, Any]:
    players = ctx.players.all_players(limit=5)
    return {
        "id": "friend_joined",
        "done": bool(players),
        "page": "overview",
        "evidence_key": "friend_done" if players else "friend_none",
        "evidence": {"count": len(players), "name": players[0]["username"] if players else None},
    }


def _alerts_on(ctx: ServerContext) -> dict[str, Any]:
    """Done once at least one channel is switched on *and* could deliver:
    a switch with no webhook or no mail password sends nothing, so it
    doesn't count. A phone app with lock-screen alerts on counts too."""
    from .notifications import apns, fcm

    config = ctx.core.config
    notifications = config.notifications
    channels = [
        name
        for name, on in (
            ("discord", notifications.discord_enabled and bool(config.discord_webhook)),
            ("email", notifications.email_enabled and bool(config.smtp_password)),
            (
                "push",
                bool(fcm.count(ctx.core.db))
                and (fcm.configured(config) or apns.configured(config)),
            ),
        )
        if on
    ]
    return {
        "id": "alerts_on",
        "done": bool(channels),
        "page": "app-settings",
        "evidence_key": "alerts_done" if channels else "alerts_none",
        "evidence": {"channels": channels},
    }


def _phones(ctx: ServerContext) -> dict[str, Any] | None:
    """Only after the desktop app's Tailscale step was skipped: done once
    the agent really listens on its Tailscale address."""
    watch = ctx.core.tailscale
    if not watch.saved().get("skipped"):
        return None
    return {
        "id": "phones",
        "done": watch.moved,
        "page": "tailscale",
        "evidence_key": "phones_done" if watch.moved else "phones_none",
        "evidence": {"address": ctx.core.config.network.host if watch.moved else None},
    }


def _discord(ctx: ServerContext) -> dict[str, Any] | None:
    """Only once a bot token is set: done when the bot is connected, this
    server has a Discord server set up for it, and nothing there is wrong."""
    bot = ctx.core.discord
    if bot is None:
        return None
    from .discord import layout

    connected = bot.gateway.state == "connected"
    guilds = [g for g in bot.guilds if ctx.server_id in layout.servers_of(ctx.core.db, g)]
    problem = bot.problem()
    done = connected and bool(guilds) and problem is None
    key = (
        "discord_done"
        if done
        else "discord_offline"
        if not connected
        else "discord_not_set_up"
        if not guilds
        else "discord_problem"
    )
    return {
        "id": "discord",
        "done": done,
        "page": "discord",
        "evidence_key": key,
        "evidence": {"name": bot.guilds[guilds[0]]["name"] if guilds else None},
    }


ITEMS = (_server_ready, _backup_taken, _friend_joined, _alerts_on, _phones, _discord)


def status(ctx: ServerContext) -> dict[str, Any]:
    items = [item for check in ITEMS if (item := check(ctx))]
    done = [i for i in items if i["done"]]
    finished = len(done) == len(items)
    saved = ctx.db.get_setting(STATE_KEY, {}) or {}
    if finished and not saved.get("finished"):
        # Remembered, so the card does not come back if an item later stops
        # being true (alerts switched off again, say).
        ctx.db.set_setting(STATE_KEY, {**saved, "finished": True})
        saved = {**saved, "finished": True}
    return {
        "items": items,
        "done": len(done),
        "total": len(items),
        "show": not saved.get("dismissed") and not saved.get("finished"),
        "dismissed": bool(saved.get("dismissed")),
        "finished": bool(saved.get("finished")),
    }


def dismiss(ctx: ServerContext) -> dict[str, Any]:
    saved = ctx.db.get_setting(STATE_KEY, {}) or {}
    ctx.db.set_setting(STATE_KEY, {**saved, "dismissed": True})
    return status(ctx)
