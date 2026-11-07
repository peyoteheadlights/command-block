"""Two console watchers for the Protection card.

**Player lists changed outside the app.** Minecraft keeps its operators,
whitelist and bans in ops.json, whitelist.json and banned-players.json. The
app sends its changes through the console, which only it can write to, so
Minecraft answers them with a plain line ("Made Bob a server operator").
Anyone else's change shows up two ways, and both send an alert naming who
was added or removed:

  * while the server runs, an op in the game or an RCON client gets the
    same answer in brackets: "[Steve: Made Bob a server operator]"
  * while it was off, a file was edited: the names in each file are kept
    when the server stops (and after a restore) and compared when it starts

Operators have their own alert switch ("ops_changed", on by default), the
whitelist and bans share one ("lists_changed"); each can be muted in Alerts.

**The chat spam rule.** When several names that are new to the server say
the same line within a short time (5 names in 60 seconds unless changed in
settings), the whitelist turns on with Minecraft's own command and the owner
gets an alert with the measured count. Names are new when this server first
saw them join less than a day ago.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from .events import Event
from .minecraft.playeractions import read_list

if TYPE_CHECKING:
    from .core import ServerContext
    from .minecraft.console import ConsoleLine

LISTS = ("ops", "whitelist", "banned")
SNAPSHOT_KEY = "player_lists"
NEW_FOR = 24 * 3600  # a name first seen this recently is new
SPAM_QUIET = 600  # after the rule fires, it waits this long before firing again
_NAME = r"(?P<name>\.?[A-Za-z0-9_]{1,16})"
# Someone else's change, as Minecraft echoes it to the console: [Steve: ...]
OUTSIDE = (
    (re.compile(rf"^\[(?P<by>[^\]:]+): Made {_NAME} a server operator\]$"), "ops", "added"),
    (
        re.compile(rf"^\[(?P<by>[^\]:]+): Made {_NAME} no longer a server operator\]$"),
        "ops",
        "removed",
    ),
    (re.compile(rf"^\[(?P<by>[^\]:]+): Added {_NAME} to the whitelist\]$"), "whitelist", "added"),
    (
        re.compile(rf"^\[(?P<by>[^\]:]+): Removed {_NAME} from the whitelist\]$"),
        "whitelist",
        "removed",
    ),
    (re.compile(rf"^\[(?P<by>[^\]:]+): Banned {_NAME}(?::.*)?\]$"), "banned", "added"),
    (re.compile(rf"^\[(?P<by>[^\]:]+): Unbanned {_NAME}\]$"), "banned", "removed"),
)
WORDS = {"ops": "operators", "whitelist": "the whitelist", "banned": "the ban list"}


def names(ctx: ServerContext) -> dict[str, list[str]] | None:
    """The names in each list now, or None for a server that has no such
    files (Bedrock) or hasn't written them yet."""
    if ctx.config.server_type.dialect != "java":
        return None
    found = {}
    for which in LISTS:
        players = read_list(ctx.config.server_dir, which)["players"]
        if players is None:
            return None
        found[which] = sorted({p["name"] for p in players}, key=str.lower)
    return found


def remember(ctx: ServerContext) -> None:
    """Keep the lists as they are now, to compare with at the next start."""
    found = names(ctx)
    if found is not None:
        ctx.db.set_setting(SNAPSHOT_KEY, found)


async def compare(ctx: ServerContext) -> None:
    """At start: alert on what changed in the files since they were kept."""
    before, now = ctx.db.get_setting(SNAPSHOT_KEY), names(ctx)
    if now is None:
        return
    ctx.db.set_setting(SNAPSHOT_KEY, now)
    if not isinstance(before, dict):
        return  # the first start the app has seen: nothing to compare with
    for which in LISTS:
        old, new = set(before.get(which) or []), set(now[which])
        for name in sorted(new - old, key=str.lower):
            await alert(ctx, which, "added", name, None)
        for name in sorted(old - new, key=str.lower):
            await alert(ctx, which, "removed", name, None)


async def heard(ctx: ServerContext, line: ConsoleLine) -> None:
    """While running: an op in the game or RCON changed a list."""
    if line.source != "stdout":
        return
    text = (line.message or "").strip()
    if not text.startswith("["):
        return
    for pattern, which, how in OUTSIDE:
        found = pattern.match(text)
        if found:
            await alert(ctx, which, how, found["name"], found["by"].strip())
            return


async def alert(ctx: ServerContext, which: str, how: str, name: str, by: str | None) -> None:
    where = WORDS[which]
    verb = "added to" if how == "added" else "removed from"
    if which == "ops":
        verb = "made an operator" if how == "added" else "no longer an operator"
        message = f"{name} was {verb} on {ctx.name}"
    else:
        message = f"{name} was {verb} {where} on {ctx.name}"
    message += (
        f" by {by}, not through the app."
        if by
        else " while the server was off, not through the app."
    )
    await ctx.bus.publish(
        Event(
            type="ops_changed" if which == "ops" else "lists_changed",
            level="warn",
            message=message,
            data={"list": which, "change": how, "username": name, "by": by},
        )
    )


# ---------------------------------------------------------------- chat spam
class ChatSpam:
    """The chat spam rule for one server."""

    def __init__(self, ctx: ServerContext):
        self.ctx = ctx
        self.said: dict[str, dict[str, float]] = {}  # line -> {new name: when}
        self.fired_at = 0.0
        self._new: dict[str, bool] = {}

    def is_new(self, name: str, now: float) -> bool:
        if name not in self._new:
            row = self.ctx.db.query_one(
                "SELECT first_seen FROM players WHERE server_id = ? AND username = ?",
                (self.ctx.server_id, name),
            )
            first = row["first_seen"] if row else None
            self._new[name] = first is None or now - first < NEW_FOR
        return self._new[name]

    async def heard(self, name: str, text: str, now: float | None = None) -> dict[str, Any] | None:
        settings = self.ctx.core.config.security
        now = now if now is not None else time.time()
        if not settings.chat_spam_rule or not name or not self.is_new(name, now):
            return None
        window = settings.chat_spam_seconds
        line = " ".join(text.lower().split())[:200]
        for said in list(self.said):
            self.said[said] = {n: t for n, t in self.said[said].items() if now - t <= window}
            if not self.said[said]:
                del self.said[said]
        who = self.said.setdefault(line, {})
        who[name] = now
        if len(who) < settings.chat_spam_names or now - self.fired_at < SPAM_QUIET:
            return None
        self.fired_at = now
        from .protection import live_whitelist_on

        turned_on = await live_whitelist_on(self.ctx)
        found = {"names": sorted(who), "count": len(who), "seconds": int(window), "line": line}
        await self.ctx.bus.publish(
            Event(
                type="chat_spam",
                level="warn",
                message=(
                    f"{len(who)} new names said the same line on {self.ctx.name} within "
                    f"{int(window)} seconds"
                    + (", so the whitelist was turned on." if turned_on else ".")
                ),
                data={**found, "whitelist_on": turned_on},
            )
        )
        self.said.pop(line, None)
        return found
