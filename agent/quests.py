"""Quests: things to do on a Minecraft server for a reward (Phase 11).

The owner and people with "Quests and events" make them (desktop app, phone
app or /quest create); Discord members join with /quest join. Progress only
comes from what Command Block can measure: a stat or an advancement in the
world's stats files, hours played and joins from our own player sessions,
items in the inventory (counted with ``clear <name> <item> 0``, which never
takes anything), or a person pressing Approve. It's counted from when the
member joined (from the start of the day, week, month or year for a quest
that repeats) and added up over their linked accounts on that server.

Rewards only come from fixed templates (ADR 0004): a Discord role, an item,
Minecraft XP or Level XP. A quest never holds a command; the item and XP
commands are built here from validated values and pass the console's own
validation. A player who is offline gets them on their next join.

Nothing here knows about Discord: Discord roles and the board are the bot's
(agent/discord/bot.py), which reads ``quest_rewards`` and ``board``.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import levels
from .events import Event
from .minecraft.commands import CommandError
from .minecraft.playeractions import DONE, FAILED, SENT, UNCHANGED, Action, PlayerActionError
from .stats import advancement_done, raw_files

if TYPE_CHECKING:
    from .core import AgentCore, ServerContext

log = logging.getLogger("msc.quests")

KINDS = ("stat", "advancement", "playtime", "joins", "items", "approval")
FROM_FILES = ("stat", "advancement")  # need the world's stats files
CHECKED = (*FROM_FILES, "playtime", "joins")  # progress checked every minute
REPEATS = ("once", "daily", "weekly", "monthly", "yearly")
TITLE_MAX = 80
DETAILS_MAX = 300
MAX_COUNT = 100_000
MAX_ITEMS_HELD = 36 * 64  # a full inventory
MAX_ACTIVE = 25  # per server: what one board message can show
MAX_SHOWN = 50  # members listed per quest in the apps
# Reward templates: the only things a quest can give, with their caps.
MAX_ITEM_COUNT = 64
MAX_XP_LEVELS = 30
MAX_LEVEL_XP = 500
MAX_STACK = 10  # "Dragon Slayer ×10" at most (Discord allows 250 roles)
SUGGESTIONS_PER_MEMBER = 3
SUGGESTIONS_PER_SERVER = 50
SCREENSHOTS = "quest-screenshots"
# An item, stat or advancement id: "minecraft:diamond", "create:zinc_ore",
# "minecraft:end/kill_dragon".
ID_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
ROLE_ID_RE = re.compile(r"^\d{5,25}$")
WAIT = 20.0  # seconds a command waits for the console's answer

# Java's answers, as vanilla (and Paper, Fabric, Forge) print them.
JAVA_REWARDS = {
    "item": Action(
        "give {name} {item} {count}",
        done=(r"Gave \d+ \[.+\] to {name}\b",),
        failed=(r"No player was found", r"Unknown item"),
    ),
    "xp": Action(
        "xp add {name} {levels} levels",
        done=(r"Gave \d+ experience levels to {name}\b",),
        failed=(r"No player was found",),
    ),
}
# Bedrock Dedicated Server: its own syntax, and answers nobody has checked
# yet, so these stay "sent" rather than claiming they worked.
BEDROCK_REWARDS = {
    "item": Action('give "{name}" {item} {count}', done=()),
    "xp": Action('xp {levels}L "{name}"', done=()),
}
# Count only: with a count of 0, clear takes nothing.
ITEM_CHECK = Action(
    "clear {name} {item} 0",
    done=(r"Found (\d+) matching items?(?:\(s\))? on player {name}\b",),
    unchanged=(r"No items were found on player {name}\b",),
    failed=(r"No player was found",),
)


class QuestError(ValueError):
    """Written for people: shown in Discord and the apps as it is."""


# -- checking what's typed -------------------------------------------------------
def one_line(value: Any, most: int, what: str) -> str:
    words = " ".join(str(value or "").split())
    if len(words) > most:
        raise QuestError(f"Keep the {what} to {most} characters.")
    return words


def parse_id(value: Any, what: str) -> str:
    """An id as Minecraft writes it; "diamond" means "minecraft:diamond"."""
    found = str(value or "").strip().lower()
    if found and ":" not in found:
        found = f"minecraft:{found}"
    if not ID_RE.match(found):
        raise QuestError(f'That isn\'t {what} id. Ids look like "minecraft:diamond".')
    return found


def parse_stat(value: Any) -> str:
    """A stat as "<type> <name>": "mined:diamond_ore" or the scoreboard's
    "minecraft.mined:minecraft.diamond_ore" both mean "minecraft:mined
    minecraft:diamond_ore" (how the stats file keeps it)."""
    kind, _, name = str(value or "").strip().lower().partition(":")
    if not kind or not name:
        raise QuestError('Write the stat as "mined:diamond_ore" or "killed:zombie".')
    parts = [parse_id(p.replace(".", ":", 1), "a stat") for p in (kind, name)]
    return " ".join(parts)


def allowed_kinds(ctx: ServerContext) -> list[str]:
    """Bedrock has no stats files and no clear command that counts, so its
    quests are the ones from our own player sessions, and approval."""
    server_type = ctx.config.server_type
    return [
        k
        for k in KINDS
        if (k not in FROM_FILES or server_type.stats_files)
        and (k != "items" or server_type.dialect == "java")
    ]


def check_rewards(rewards: Any) -> list[dict[str, Any]]:
    """Each reward is one of the templates, with its values in range. A
    reward can't hold anything else (no command, no text)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for given in rewards or []:
        if not isinstance(given, dict) or given.get("kind") in seen:
            raise QuestError("Each kind of reward can only be given once.")
        kind = given.get("kind")
        seen.add(str(kind))

        def number(key: str, top: int, what: str, given: dict = given) -> int:
            try:
                value = int(given.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if not 1 <= value <= top:
                raise QuestError(f"{what} goes from 1 to {top}.")
            return value

        if kind == "role":
            role = str(given.get("role_id") or "")
            if not ROLE_ID_RE.match(role):
                raise QuestError("Pick a Discord role.")
            out.append({"kind": "role", "role_id": role})
        elif kind == "item":
            out.append(
                {
                    "kind": "item",
                    "item": parse_id(given.get("item"), "an item"),
                    "count": number("count", MAX_ITEM_COUNT, "The number of items"),
                }
            )
        elif kind == "xp":
            out.append({"kind": "xp", "levels": number("levels", MAX_XP_LEVELS, "Minecraft XP")})
        elif kind == "level_xp":
            out.append({"kind": "level_xp", "amount": number("amount", MAX_LEVEL_XP, "Level XP")})
        else:
            raise QuestError("A reward is a Discord role, an item, Minecraft XP or Level XP.")
    return out


def period_of(repeat: str, now: datetime.datetime | None = None) -> str:
    """The period a quest's progress counts in, on the PC's clock."""
    day = (now or datetime.datetime.now()).date()
    return {
        "once": "once",
        "daily": day.isoformat(),
        "weekly": (day - datetime.timedelta(days=day.weekday())).isoformat(),
        "monthly": day.strftime("%Y-%m"),
        "yearly": str(day.year),
    }[repeat]


def end_of(day: str | None) -> float | None:
    """The end of a "last day" (00:00 after it, PC clock), or None."""
    if not day:
        return None
    try:
        last = datetime.date.fromisoformat(str(day))
    except ValueError:
        raise QuestError("Write the last day as 2026-12-31.") from None
    ends = datetime.datetime.combine(last + datetime.timedelta(days=1), datetime.time())
    if ends.timestamp() <= time.time():
        raise QuestError("The last day has already gone.")
    return ends.timestamp()


def check_quest(ctx: ServerContext, wanted: dict[str, Any]) -> dict[str, Any]:
    """A new quest, every value checked. Discord roles are checked against
    Discord by the caller (they need the bot)."""
    title = one_line(wanted.get("title"), TITLE_MAX, "title")
    if not title:
        raise QuestError("Give the quest a title.")
    kind = wanted.get("kind")
    if kind not in KINDS:
        raise QuestError("Pick what the quest asks for.")
    if kind not in allowed_kinds(ctx):
        raise QuestError(
            f"{ctx.name} doesn't keep stats files or count items, so its quests are about "
            "playtime, joins or a moderator's approval."
        )
    repeat = wanted.get("repeat") or "once"
    if repeat not in REPEATS:
        raise QuestError("Pick how often the quest comes back.")
    if kind == "advancement" and repeat != "once":
        raise QuestError("An advancement is earned once, so that quest can't come back.")
    goal = ""
    if kind == "stat":
        goal = parse_stat(wanted.get("goal"))
    elif kind in ("advancement", "items"):
        goal = parse_id(
            wanted.get("goal"), "an advancement" if kind == "advancement" else "an item"
        )
    try:
        count = int(wanted["count"]) if wanted.get("count") is not None else 1
    except (TypeError, ValueError):
        count = 0
    top = MAX_ITEMS_HELD if kind == "items" else MAX_COUNT
    if kind in ("advancement", "approval"):
        count = 1
    elif not 1 <= count <= top:
        raise QuestError(f"The number to reach goes from 1 to {top:,}.")
    return {
        "title": title,
        "details": one_line(wanted.get("details"), DETAILS_MAX, "details"),
        "kind": kind,
        "goal": goal,
        "count": count,
        "repeat": repeat,
        "ends_at": end_of(wanted.get("ends")),
        "rewards": check_rewards(wanted.get("rewards")),
    }


class Quests:
    def __init__(self, core: AgentCore):
        self.core = core
        self.db = core.db
        self._lock = asyncio.Lock()  # one check at a time
        # (world, uuid) -> (the files' times, stats, advancements, saved at):
        # a file is read again only when Minecraft saved it again.
        self._files: dict[tuple[str, str], tuple[Any, dict, dict, float | None]] = {}
        self._read_since: set[tuple[str, str]] = set()  # kept at the next check
        self._jobs: set[asyncio.Task] = set()
        core.bus.subscribe(self.handle)

    def stop(self) -> None:
        self.core.bus.unsubscribe(self.handle)
        for task in list(self._jobs):
            task.cancel()

    def spawn(self, work) -> None:
        task = asyncio.ensure_future(work)
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    # -- reading ----------------------------------------------------------------
    def active(self, server_id: str | None = None) -> list[dict[str, Any]]:
        """Quests going on now, on servers that are still there."""
        rows = self.db.query(
            "SELECT * FROM quests WHERE ended_at IS NULL AND (ends_at IS NULL OR ends_at > ?) "
            + ("AND server_id = ? " if server_id else "")
            + "ORDER BY id",
            (time.time(), server_id) if server_id else (time.time(),),
        )
        return [r for r in rows if r["server_id"] in self.core.servers]

    def get(self, server_id: str, quest_id: Any) -> dict[str, Any]:
        found = next((q for q in self.active(server_id) if str(q["id"]) == str(quest_id)), None)
        if found is None:
            raise QuestError(f"There's no quest {quest_id} going on here. The board lists them.")
        return found

    def view(self, quest: dict[str, Any]) -> dict[str, Any]:
        """A quest as the apps and the board show it: its rewards, the
        members on it and how far each is in this period."""
        period = period_of(quest["repeat"])
        rows = self.db.query(
            "SELECT p.discord_id, p.progress, p.saved_at, p.finished_at, "
            "(SELECT discord_name FROM discord_links l WHERE l.discord_id = p.discord_id "
            "LIMIT 1) AS name FROM quest_progress p WHERE p.quest_id = ? AND p.period = ? "
            "ORDER BY p.finished_at IS NULL, p.finished_at, p.progress DESC",
            (quest["id"], period),
        )
        joined = self.db.query_one(
            "SELECT COUNT(DISTINCT discord_id) AS n FROM quest_progress WHERE quest_id = ?",
            (quest["id"],),
        )
        saved = [r["saved_at"] for r in rows if r["saved_at"]]
        return {
            **quest,
            "rewards": json.loads(quest["rewards"]),
            "period": period,
            "joined": int(joined["n"] if joined else 0),
            "saved_at": max(saved) if saved and quest["kind"] in FROM_FILES else None,
            "members": [
                {
                    "discord_id": r["discord_id"],
                    "name": r["name"] or "",
                    "progress": min(r["progress"], quest["count"]),
                    "finished_at": r["finished_at"],
                }
                for r in rows[:MAX_SHOWN]
            ],
        }

    def board(self, server_id: str) -> list[dict[str, Any]]:
        return [self.view(q) for q in self.active(server_id)]

    def of_member(self, server_id: str, member: str) -> list[dict[str, Any]]:
        """The member's quests going on here, with this period's progress."""
        out = []
        for quest in self.active(server_id):
            row = self.db.query_one(
                "SELECT * FROM quest_progress WHERE quest_id = ? AND discord_id = ? "
                "ORDER BY period = ? DESC LIMIT 1",
                (quest["id"], member, period_of(quest["repeat"])),
            )
            if row is not None:
                current = row["period"] == period_of(quest["repeat"])
                out.append(
                    {
                        **quest,
                        "progress": row["progress"] if current else 0,
                        "saved_at": row["saved_at"] if current else None,
                        "finished_at": row["finished_at"] if current else None,
                        "times": self.times_finished(quest["id"], member),
                    }
                )
        return out

    def times_finished(self, quest_id: int, member: str) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM quest_progress WHERE quest_id = ? AND discord_id = ? "
            "AND finished_at IS NOT NULL",
            (quest_id, member),
        )
        return int(row["n"] if row else 0)

    # -- making and ending ------------------------------------------------------------
    async def make(self, ctx: ServerContext, wanted: dict[str, Any], by: str) -> dict[str, Any]:
        quest = check_quest(ctx, wanted)
        if len(self.active(ctx.server_id)) >= MAX_ACTIVE:
            raise QuestError(f"{ctx.name} has {MAX_ACTIVE} quests going on. End one first.")
        suggestion = wanted.get("suggestion_id")
        asked = self.drop_suggestion(ctx.server_id, suggestion) if suggestion else None
        made = self.db.insert(
            "quests",
            {
                **quest,
                "server_id": ctx.server_id,
                "rewards": json.dumps(quest["rewards"]),
                "created_at": time.time(),
                "created_by": by,
            },
        )
        found = self.get(ctx.server_id, made)
        if asked:
            self.tell(
                asked,
                f'your quest idea "{asked["title"]}" is quest {made} on {ctx.name} now. '
                f"Join it with /quest join {made}.",
            )
        await ctx.bus.publish(
            Event(
                type="quest_added",
                message=f"New quest on {ctx.name}: {quest['title']}",
                data={"quest_id": made, "title": quest["title"], "by": by},
            )
        )
        return found

    def end(self, server_id: str, quest_id: Any) -> dict[str, Any]:
        """Ended, not deleted: finished quests still count for stacked
        Discord roles. Its waiting claims and their screenshots go."""
        quest = self.get(server_id, quest_id)
        self.db.execute("UPDATE quests SET ended_at = ? WHERE id = ?", (time.time(), quest["id"]))
        for claim in self.db.query("SELECT * FROM quest_claims WHERE quest_id = ?", (quest["id"],)):
            self._drop_claim(claim)
        return quest

    # -- members ---------------------------------------------------------------------
    def accounts(self, member: str, server_id: str) -> list[dict[str, Any]]:
        """The member's linked accounts that played on this server."""
        return self.db.query(
            "SELECT p.username, p.uuid, p.total_seconds, p.online, p.session_started, p.sessions "
            "FROM discord_links l JOIN players p ON p.uuid = l.uuid AND p.server_id = ? "
            "WHERE l.discord_id = ? ORDER BY p.username",
            (server_id, member),
        )

    async def join(self, ctx: ServerContext, quest_id: Any, member: str) -> dict[str, Any]:
        quest = self.get(ctx.server_id, quest_id)
        if not self.core.links.of_member(member):
            raise QuestError(
                "Link your Minecraft account with /link first: quests count your play."
            )
        if self.db.query_one(
            "SELECT 1 FROM quest_progress WHERE quest_id = ? AND discord_id = ?",
            (quest["id"], member),
        ):
            raise QuestError(
                f"You're on quest {quest['id']} already. /quest progress shows how far you are."
            )
        async with self._lock:
            await self._row(quest, ctx, member, period_of(quest["repeat"]))
        return quest

    async def _row(
        self, quest: dict[str, Any], ctx: ServerContext, member: str, period: str
    ) -> dict[str, Any]:
        """The member's progress row for a period, made (with today's
        numbers as the start) when it isn't there yet."""
        key = (quest["id"], member, period)
        row = self.db.query_one(
            "SELECT * FROM quest_progress WHERE quest_id = ? AND discord_id = ? AND period = ?",
            key,
        )
        if row is None:
            numbers, saved_at = await self._numbers(quest, ctx, member)
            self.db.execute(
                "INSERT OR IGNORE INTO quest_progress (quest_id, discord_id, period, baseline, "
                "saved_at) VALUES (?, ?, ?, ?, ?)",
                (*key, json.dumps(numbers), saved_at),
            )
            row = self.db.query_one(
                "SELECT * FROM quest_progress WHERE quest_id = ? AND discord_id = ? AND period = ?",
                key,
            )
        assert row is not None
        return row

    # -- measuring ---------------------------------------------------------------------
    async def _numbers(
        self, quest: dict[str, Any], ctx: ServerContext, member: str
    ) -> tuple[dict[str, int], float | None]:
        """Each linked account's number for the quest now (account id ->
        number), and when the files they came from were saved."""
        out: dict[str, int] = {}
        saved: list[float] = []
        now = time.time()
        for account in self.accounts(member, ctx.server_id):
            uuid = str(account["uuid"])
            kind = quest["kind"]
            if kind == "playtime":
                live = now - account["session_started"] if account["online"] else 0
                out[uuid] = int(float(account["total_seconds"] or 0) + max(live or 0, 0))
            elif kind == "joins":
                out[uuid] = int(account["sessions"] or 0)
            elif kind in FROM_FILES:
                sections, earned, saved_at = await self._read(ctx, uuid)
                if saved_at:
                    saved.append(saved_at)
                if kind == "advancement":
                    out[uuid] = int(advancement_done(earned, quest["goal"]))
                else:
                    section, _, name = quest["goal"].partition(" ")
                    value = (sections.get(section) or {}).get(name)
                    out[uuid] = value if isinstance(value, int) else 0
        return out, max(saved) if saved else None

    async def _read(self, ctx: ServerContext, uuid: str) -> tuple[dict, dict, float | None]:
        from .addons import world_path

        world = world_path(ctx)

        def times() -> tuple[float | None, ...]:
            out: list[float | None] = []
            for folder in ("stats", "advancements"):
                try:
                    out.append(os.stat(world / folder / f"{uuid}.json").st_mtime)
                except (OSError, ValueError):
                    out.append(None)
            return tuple(out)

        key = (str(world), uuid)
        self._read_since.add(key)
        now = await asyncio.to_thread(times)
        cached = self._files.get(key)
        if cached is None or cached[0] != now:
            cached = (now, *await asyncio.to_thread(raw_files, Path(world), uuid))
            self._files[key] = cached
        return cached[1], cached[2], cached[3]

    @staticmethod
    def progress(quest: dict[str, Any], numbers: dict[str, int], start: dict[str, int]) -> int:
        if quest["kind"] == "advancement":
            return int(any(numbers.values()))
        gained = sum(max(v - start.get(uuid, v), 0) for uuid, v in numbers.items())
        return gained // 3600 if quest["kind"] == "playtime" else gained

    async def check(self) -> bool:
        """Once a minute (the bot's loop): everyone's progress on the quests
        that check themselves, finishing the ones who got there. True when
        something changed (the board is edited then)."""
        changed = False
        async with self._lock:
            self._read_since = set()
            for quest in self.active():
                ctx = self.core.servers.get(quest["server_id"])
                if ctx is None:
                    continue
                period = period_of(quest["repeat"])
                members = [
                    r["discord_id"]
                    for r in self.db.query(
                        "SELECT DISTINCT discord_id FROM quest_progress WHERE quest_id = ?",
                        (quest["id"],),
                    )
                ]
                for member in members:
                    row = await self._row(quest, ctx, member, period)
                    if quest["kind"] not in CHECKED or row["finished_at"]:
                        continue
                    numbers, saved_at = await self._numbers(quest, ctx, member)
                    start = json.loads(row["baseline"])
                    if set(numbers) - set(start):  # an account linked since: counts from now
                        start = {**numbers, **start}
                        self.db.execute(
                            "UPDATE quest_progress SET baseline = ? WHERE quest_id = ? AND "
                            "discord_id = ? AND period = ?",
                            (json.dumps(start), quest["id"], member, period),
                        )
                    now = self.progress(quest, numbers, start)
                    if now != row["progress"] or saved_at != row["saved_at"]:
                        self.db.execute(
                            "UPDATE quest_progress SET progress = ?, saved_at = ? WHERE "
                            "quest_id = ? AND discord_id = ? AND period = ?",
                            (now, saved_at, quest["id"], member, period),
                        )
                        changed = changed or now != row["progress"]
                    if now >= quest["count"]:
                        changed = await self.finish(quest, ctx, member, period) or changed
                # Last period's unfinished rows say nothing any more.
                self.db.execute(
                    "DELETE FROM quest_progress WHERE quest_id = ? AND period != ? "
                    "AND finished_at IS NULL",
                    (quest["id"], period),
                )
            # Only the files of people still on a quest stay cached.
            for key in set(self._files) - self._read_since:
                del self._files[key]
        return changed

    async def finish(
        self, quest: dict[str, Any], ctx: ServerContext, member: str, period: str
    ) -> bool:
        """The member finished: their rewards are owed, once. False when
        they had finished this period already."""
        done = self.db.execute(
            "UPDATE quest_progress SET finished_at = ?, progress = MAX(progress, ?) "
            "WHERE quest_id = ? AND discord_id = ? AND period = ? AND finished_at IS NULL",
            (time.time(), quest["count"], quest["id"], member, period),
        )
        if not done.rowcount:
            return False
        now = time.time()
        for reward in json.loads(quest["rewards"]):
            state = "waiting"
            if reward["kind"] == "level_xp":
                levels.award(self.db, member, reward["amount"], "quest", f"{quest['id']}:{period}")
                state = "done"
            self.db.execute(
                "INSERT OR IGNORE INTO quest_rewards (quest_id, discord_id, period, server_id, "
                "kind, value, state, created_at, given_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    quest["id"],
                    member,
                    period,
                    ctx.server_id,
                    reward["kind"],
                    json.dumps(reward),
                    state,
                    now,
                    now if state == "done" else None,
                ),
            )
        name = next((r["name"] for r in self.core.links.of_member(member)), "Someone")
        await ctx.bus.publish(
            Event(
                type="quest_finished",
                level="success",
                message=f"{name} finished the quest {quest['title']}",
                data={"quest_id": quest["id"], "title": quest["title"], "discord_id": member},
            )
        )
        self.spawn(self.give_waiting(ctx, member))
        return True

    # -- claims: "I'm done" -----------------------------------------------------------
    async def done(
        self,
        ctx: ServerContext,
        quest_id: Any,
        member: str,
        member_name: str,
        note: str = "",
        channel_id: str | None = None,
        screenshot: tuple[bytes, str] | None = None,
    ) -> str:
        """/quest done: count the items, or send the claim for approval.
        Returns what to tell the member."""
        quest = self.get(ctx.server_id, quest_id)
        if quest["kind"] in CHECKED:
            raise QuestError(
                "This quest checks itself when Minecraft saves your progress (every few "
                "minutes, and when you leave). /quest progress shows how far you are."
            )
        if not self.core.links.of_member(member):
            raise QuestError(
                "Link your Minecraft account with /link first: quests count your play."
            )
        period = period_of(quest["repeat"])
        async with self._lock:
            row = await self._row(quest, ctx, member, period)
        if row["finished_at"]:
            raise QuestError(f"You've finished quest {quest['id']} already{self._again(quest)}.")
        if quest["kind"] == "items":
            return await self._count_items(quest, ctx, member, period)
        note = one_line(note, DETAILS_MAX, "note")
        made = self.db.execute(
            "INSERT OR IGNORE INTO quest_claims (quest_id, discord_id, discord_name, period, "
            "note, channel_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (quest["id"], member, member_name[:100], period, note, channel_id, time.time()),
        )
        if not made.rowcount:
            raise QuestError("Your claim is already waiting for the owner or a moderator.")
        claim_id = int(made.lastrowid or 0)
        if screenshot:
            data, ext = screenshot
            folder = Path(self.core.config.data_dir) / SCREENSHOTS
            name = f"{claim_id}.{ext}"  # our own number: never a name from Discord
            await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread((folder / name).write_bytes, data)
            self.db.execute("UPDATE quest_claims SET screenshot = ? WHERE id = ?", (name, claim_id))
        await ctx.bus.publish(
            Event(
                type="quest_claim",
                level="info",
                message=f"{member_name} says they finished {quest['title']}",
                data={
                    "claim_id": claim_id,
                    "quest_id": quest["id"],
                    "title": quest["title"],
                    "discord_name": member_name,
                },
            )
        )
        return (
            f"Sent. The owner or a moderator of {ctx.name} will look at it; "
            "you'll hear here when they do."
        )

    @staticmethod
    def _again(quest: dict[str, Any]) -> str:
        return {
            "once": "",
            "daily": "; it comes back tomorrow",
            "weekly": "; it comes back on Monday",
            "monthly": "; it comes back next month",
            "yearly": "; it comes back next year",
        }[quest["repeat"]]

    async def _count_items(
        self, quest: dict[str, Any], ctx: ServerContext, member: str, period: str
    ) -> str:
        """Ask the server how many of the item each online linked account
        holds (clear with a count of 0 takes nothing)."""
        names = [a["username"] for a in self.accounts(member, ctx.server_id) if a["online"]]
        if not names or ctx.server.state.value != "ONLINE":
            raise QuestError(
                f"Join {ctx.name} as one of your linked accounts, with the items on you, "
                "then try again."
            )
        best = None
        for name in names:
            command = ITEM_CHECK.command.format(name=name, item=quest["goal"])
            pending = await ctx.player_actions.send_built("item_check", name, command, ITEM_CHECK)
            state = await self.answer(pending, 5.0)
            if state == DONE:
                found = re.search(r"Found (\d+)", pending.message or "")
                count = int(found.group(1)) if found else None
            elif state == UNCHANGED:
                count = 0
            else:
                continue  # left, or no answer: never a guess
            if count is not None:
                best = max(best or 0, count)
        if best is None:
            raise QuestError(f"{ctx.name} didn't answer. Try again in a moment.")
        self.db.execute(
            "UPDATE quest_progress SET progress = ?, saved_at = ? WHERE quest_id = ? AND "
            "discord_id = ? AND period = ? AND finished_at IS NULL",
            (min(best, quest["count"]), time.time(), quest["id"], member, period),
        )
        item = quest["goal"].removeprefix("minecraft:")
        if best < quest["count"]:
            return f"You have {best} of the {quest['count']} {item} the quest asks for. Keep going!"
        await self.finish(quest, ctx, member, period)
        return f"You have {best} {item}: quest {quest['id']} done! Your items stay with you."

    @staticmethod
    async def answer(pending, wait: float) -> str:
        """The console's answer to a command we sent, waiting up to ``wait``."""
        for _ in range(int(wait * 10)):
            if pending.state != SENT:
                break
            await asyncio.sleep(0.1)
        return pending.state

    def claims(self, server_id: str) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT c.*, q.title, q.details FROM quest_claims c JOIN quests q ON q.id = c.quest_id "
            "WHERE q.server_id = ? ORDER BY c.created_at",
            (server_id,),
        )

    def claim(self, server_id: str, claim_id: Any) -> dict[str, Any]:
        found = next((c for c in self.claims(server_id) if str(c["id"]) == str(claim_id)), None)
        if found is None:
            raise QuestError("That claim isn't there any more.")
        return found

    def screenshot(self, server_id: str, claim_id: Any) -> Path | None:
        claim = self.claim(server_id, claim_id)
        if not claim["screenshot"]:
            return None
        path = Path(self.core.config.data_dir) / SCREENSHOTS / claim["screenshot"]
        return path if path.is_file() else None

    async def answer_claim(
        self, ctx: ServerContext, claim_id: Any, approve: bool, by: str
    ) -> dict[str, Any]:
        """Approve or Refuse. The claim is taken away first, so the app and
        a Discord button pressed at once can't both answer it."""
        claim = self.claim(ctx.server_id, claim_id)
        if not self._drop_claim(claim):
            raise QuestError("That claim isn't there any more.")
        quest = self.db.query_one("SELECT * FROM quests WHERE id = ?", (claim["quest_id"],))
        if approve and quest is not None:
            async with self._lock:
                await self._row(quest, ctx, claim["discord_id"], claim["period"])
            await self.finish(quest, ctx, claim["discord_id"], claim["period"])
        self.tell(
            claim,
            f'well done: "{claim["title"]}" is approved!'
            if approve
            else f'your claim for "{claim["title"]}" wasn\'t approved this time.',
        )
        ctx.db.audit(
            "quest_claim_" + ("approve" if approve else "refuse"),
            user=by,
            target=claim["title"],
            detail=f"Discord member {claim['discord_id']}",
        )
        return claim

    def _drop_claim(self, claim: dict[str, Any]) -> bool:
        gone = self.db.execute("DELETE FROM quest_claims WHERE id = ?", (claim["id"],)).rowcount
        if claim.get("screenshot"):
            path = Path(self.core.config.data_dir) / SCREENSHOTS / claim["screenshot"]
            path.unlink(missing_ok=True)
        return bool(gone)

    # -- suggestions -------------------------------------------------------------------
    def suggest(
        self,
        ctx: ServerContext,
        member: str,
        member_name: str,
        title: str,
        details: str,
        channel_id: str | None,
    ) -> int:
        title = one_line(title, TITLE_MAX, "title")
        if not title:
            raise QuestError("Give your idea a title.")
        details = one_line(details, DETAILS_MAX, "details")
        open_ = self.suggestions(ctx.server_id)
        if sum(s["discord_id"] == member for s in open_) >= SUGGESTIONS_PER_MEMBER:
            raise QuestError(
                f"You have {SUGGESTIONS_PER_MEMBER} ideas waiting already. "
                "Wait until the owner or a moderator looks at them."
            )
        if len(open_) >= SUGGESTIONS_PER_SERVER:
            raise QuestError("There are lots of ideas waiting already. Try again later.")
        return self.db.insert(
            "quest_suggestions",
            {
                "server_id": ctx.server_id,
                "discord_id": member,
                "discord_name": member_name[:100],
                "title": title,
                "details": details,
                "channel_id": channel_id,
                "created_at": time.time(),
            },
        )

    def suggestions(self, server_id: str) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT * FROM quest_suggestions WHERE server_id = ? ORDER BY created_at", (server_id,)
        )

    def drop_suggestion(self, server_id: str, suggestion_id: Any) -> dict[str, Any]:
        found = next(
            (s for s in self.suggestions(server_id) if str(s["id"]) == str(suggestion_id)), None
        )
        if (
            found is None
            or not self.db.execute(
                "DELETE FROM quest_suggestions WHERE id = ?", (found["id"],)
            ).rowcount
        ):
            raise QuestError("That idea isn't there any more.")
        return found

    def tell(self, asked: dict[str, Any], words: str) -> None:
        """Answer a member in the channel they asked in, when the bot runs."""
        bot = self.core.discord
        if bot is not None and asked.get("channel_id"):
            bot.spawn(bot.tell(asked["channel_id"], asked["discord_id"], words))

    # -- rewards -------------------------------------------------------------------------
    def rewards(self, server_id: str, states=("waiting", "sending", "sent", "failed")) -> list:
        marks = ",".join("?" * len(states))
        # Event rewards (agent/game_events.py) wait here too, with an event_id.
        return self.db.query(
            "SELECT r.*, COALESCE(q.title, e.title) AS title FROM quest_rewards r "
            "LEFT JOIN quests q ON q.id = r.quest_id LEFT JOIN game_events e ON e.id = r.event_id "
            f"WHERE r.server_id = ? AND r.state IN ({marks}) ORDER BY r.created_at",
            (server_id, *states),
        )

    def rewards_everywhere(self, kind: str) -> list[dict[str, Any]]:
        """Rewards of one kind still waiting, on servers that are still there."""
        rows = self.db.query(
            "SELECT * FROM quest_rewards WHERE kind = ? AND state = 'waiting' ORDER BY id", (kind,)
        )
        return [r for r in rows if r["server_id"] in self.core.servers]

    def set_reward(self, reward_id: int, state: str, answer: str | None = None) -> None:
        self.db.execute(
            "UPDATE quest_rewards SET state = ?, answer = ?, given_at = ? WHERE id = ?",
            (state, answer, time.time() if state == "done" else None, reward_id),
        )

    async def handle(self, event: Event) -> None:
        """A player who joins gets the items and Minecraft XP waiting for them."""
        if event.type == "player_joined" and event.server_id in self.core.servers:
            self.spawn(self.give_waiting(self.core.servers[event.server_id]))

    async def give_waiting(self, ctx: ServerContext, member: str | None = None) -> None:
        """Items and Minecraft XP owed on this server, to whichever linked
        account of each member is online. "waiting" -> "sending" happens in
        one UPDATE, so a reward is never sent twice."""
        if ctx.server.state.value != "ONLINE":
            return
        templates = BEDROCK_REWARDS if ctx.config.server_type.dialect == "bedrock" else JAVA_REWARDS
        for reward in self.rewards(ctx.server_id, ("waiting",)):
            if reward["kind"] not in templates or member not in (None, reward["discord_id"]):
                continue
            online = [a for a in self.accounts(reward["discord_id"], ctx.server_id) if a["online"]]
            if not online:
                continue
            if not self.db.execute(
                "UPDATE quest_rewards SET state = 'sending' WHERE id = ? AND state = 'waiting'",
                (reward["id"],),
            ).rowcount:
                continue
            name = online[0]["username"]
            value = json.loads(reward["value"])
            action = templates[reward["kind"]]
            command = action.command.format(name=name, **value)
            try:
                pending = await ctx.player_actions.send_built(reward["kind"], name, command, action)
            except (PlayerActionError, CommandError) as exc:
                # The server stopped (wait for the next join) or the values
                # aren't a command (never retried).
                stopped = isinstance(exc, PlayerActionError)
                self.set_reward(reward["id"], "waiting" if stopped else "failed", str(exc))
                continue
            state = await self.answer(pending, WAIT)
            if state == DONE:
                self.set_reward(reward["id"], "done", pending.message)
            elif state == FAILED and "No player" in (pending.message or ""):
                self.set_reward(reward["id"], "waiting")  # left just then
            elif state == FAILED:
                self.set_reward(reward["id"], "failed", pending.message)
            else:
                # No answer: maybe given, maybe not. Never sent again, and
                # never called given (house rule 1).
                self.set_reward(reward["id"], "sent", None)
