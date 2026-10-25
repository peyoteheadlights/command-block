"""The one gentle ask: a Donate / No thanks card for the owner, once.

It comes after about two weeks of real use, counted from the first time a
server really started (its console said it was done), never from install
day. That moment is remembered here, so it stays known after old events are
pruned; with no start measured, the card never shows (house rule 1).

It waits while anything is wrong: a server still being set up (its getting
started card is showing), a crash or another problem in Recommendations, a
newest backup that didn't check out, or an alert the owner hasn't read
(the bell's read marker, AuthManager.alerts_read).
Pressing either button hides it for good, on every device. Owner only; the
route's permission keeps it from moderators.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from . import checklist, recommendations

if TYPE_CHECKING:
    from .core import AgentCore
    from .database.db import Database
    from .events import Event

STATE_KEY = "support_ask"
WAIT_DAYS = 14
# Recommendations that mean something is wrong, not just a tip.
PROBLEMS = {
    "discord_problem",
    "crash",
    "no_backup",
    "old_backup",
    "disk_low",
    "java_too_old",
    "memory_over_pc",
    "offsite_copy_failed",
    "tailscale_stopped",
}
ANSWERS = ("donate", "no_thanks")


def remember_start(db: Database, ts: float | None = None) -> float | None:
    """The first real server start, saved once. A copy from before this was
    added takes the oldest start still in its events."""
    saved = db.get_setting(STATE_KEY, {}) or {}
    if saved.get("first_start") is None:
        row = db.query_one("SELECT MIN(ts) AS ts FROM events WHERE type = 'server_started'")
        found = [t for t in ((row or {}).get("ts"), ts) if t is not None]
        if not found:
            return None
        saved = {**saved, "first_start": min(found)}
        db.set_setting(STATE_KEY, saved)
    return float(saved["first_start"])


def handler(db: Database):
    async def handle(event: Event) -> None:
        if event.type == "server_started":
            remember_start(db, event.ts)

    return handle


def something_wrong(core: AgentCore) -> bool:
    for ctx in core.servers.values():
        if checklist.status(ctx)["show"]:
            return True
        backups = ctx.backups.list_backups()
        if backups and backups[0].get("status") != "ok":
            return True
        found = recommendations.evaluate(recommendations.gather(ctx))
        shown = recommendations.visible(ctx.db, found)["recommendations"]
        if any(r["id"] in PROBLEMS for r in shown):
            return True
    read = core.auth.alerts_read(core.config.admin_username)
    unread = core.db.query_one("SELECT 1 FROM app_alerts WHERE id > ? LIMIT 1", (read,))
    return unread is not None


def status(core: AgentCore) -> dict[str, Any]:
    """Whether to show the card now, and why not when it isn't shown."""
    now = time.time()
    saved = core.db.get_setting(STATE_KEY, {}) or {}
    if saved.get("answer"):
        return {"show": False, "reason": "answered"}
    first = remember_start(core.db)
    if first is None:
        return {"show": False, "reason": "never_started"}
    if now - first < WAIT_DAYS * 86400:
        return {"show": False, "reason": "too_soon"}
    if something_wrong(core):
        return {"show": False, "reason": "something_wrong"}
    return {"show": True, "reason": "ready"}


def answer(core: AgentCore, choice: str) -> dict[str, Any]:
    saved = core.db.get_setting(STATE_KEY, {}) or {}
    core.db.set_setting(STATE_KEY, {**saved, "answer": choice, "answered_at": time.time()})
    return status(core)
