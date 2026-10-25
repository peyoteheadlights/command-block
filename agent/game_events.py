"""Events and polls (Phase 11): our own, not Discord's built-in ones.

An event has a title, a start and an end on one Minecraft server, and a kind
that says how attendance is decided, only from what Command Block measures:

- ``advancement``: the advancement was earned inside the event's time (the
  advancement file keeps when each part of it was earned);
- ``online``: at least 15 minutes online inside it (our own player sessions);
- ``approval``: the app can't check it, so a suggested list (who was online,
  and who said Going) waits for the owner or a moderator to approve.

Who came gets 50 Level XP and the event's rewards, from the same templates
as quests (agent/quests.py), queued in ``quest_rewards`` so items and
Minecraft XP wait for the next join and Discord roles go through the bot.
Attendance can be fixed for 7 days after the end, and every fix is audited.

A poll has 2 to 5 answers, one vote per Discord member, and closes at a set
time. Discord members answer with buttons (agent/discord/bot.py); nothing
here knows about Discord.

In code these are "game events": ``events`` is already the Activity table
and the event bus. People only ever see "event".
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from . import levels
from .events import Event
from .quests import QuestError, check_rewards, one_line, parse_id
from .stats import earned_at

if TYPE_CHECKING:
    from .core import AgentCore, ServerContext

log = logging.getLogger("msc.game_events")

KINDS = ("advancement", "online", "approval")
ANSWERS = ("going", "maybe", "no")
TITLE_MAX = 80
DETAILS_MAX = 300
ATTEND_MINUTES = 15  # online this long inside the event counts as there
ATTEND_XP = 50  # Level XP for attending (the grilling's number)
REMIND_BEFORE = 3600.0
START_BEFORE = 600.0
COUNT_AFTER = 60.0  # after the end: Minecraft has saved the files by then
FIX_FOR = 7 * 86400.0
SHORTEST = 15 * 60.0
LONGEST = 24 * 3600.0
AHEAD = 366 * 86400.0
MAX_COMING = 25  # per server
KEEP = 8 * 86400.0  # events and polls the apps and the bot still look at
EVERY = 30.0  # seconds between checks
# Polls
ANSWER_MAX = 55  # it's a button label
QUESTION_MAX = 150
MIN_ANSWERS, MAX_ANSWERS = 2, 5
POLL_SHORTEST = 10 * 60.0
POLL_LONGEST = 7 * 86400.0
MAX_OPEN_POLLS = 10  # per server
# A tracking add-on (one that records who did what during an event) would
# go here: add-on id -> a function giving the members who came. None is
# supported in Phase 11; attendance works without one.
TRACKING_ADDONS: dict[str, Any] = {}


def parse_time(value: Any) -> float:
    """ "2026-10-30 20:00" on the PC's clock, as typed in Discord."""
    try:
        when = datetime.datetime.strptime(" ".join(str(value or "").split()), "%Y-%m-%d %H:%M")
    except ValueError:
        raise QuestError("Write the time as 2026-10-30 20:00 (the PC's clock).") from None
    return when.timestamp()


def allowed_kinds(ctx: ServerContext) -> list[str]:
    """Bedrock keeps no advancement files."""
    return [k for k in KINDS if k != "advancement" or ctx.config.server_type.stats_files]


def check_event(ctx: ServerContext, wanted: dict[str, Any]) -> dict[str, Any]:
    title = one_line(wanted.get("title"), TITLE_MAX, "title")
    if not title:
        raise QuestError("Give the event a title.")
    kind = wanted.get("kind") or "online"
    if kind not in KINDS:
        raise QuestError("Pick how attendance is counted.")
    if kind not in allowed_kinds(ctx):
        raise QuestError(
            f"{ctx.name} doesn't keep advancement files, so its events count time online "
            "or a moderator's approval."
        )
    try:
        starts, ends = float(wanted["starts_at"]), float(wanted["ends_at"])
    except (KeyError, TypeError, ValueError):
        raise QuestError("Pick when the event starts and ends.") from None
    now = time.time()
    if starts <= now:
        raise QuestError("The start has already gone.")
    if starts > now + AHEAD:
        raise QuestError("Events can start up to a year ahead.")
    if not SHORTEST <= ends - starts <= LONGEST:
        raise QuestError("An event lasts from 15 minutes to 24 hours.")
    return {
        "title": title,
        "details": one_line(wanted.get("details"), DETAILS_MAX, "details"),
        "kind": kind,
        "goal": parse_id(wanted.get("goal"), "an advancement") if kind == "advancement" else "",
        "starts_at": starts,
        "ends_at": ends,
        "start_server": 1 if wanted.get("start_server") else 0,
        "rewards": check_rewards(wanted.get("rewards")),
    }


def check_poll(wanted: dict[str, Any]) -> dict[str, Any]:
    question = one_line(wanted.get("question"), QUESTION_MAX, "question")
    if not question:
        raise QuestError("Write the question.")
    answers = [one_line(a, ANSWER_MAX, "answers") for a in wanted.get("answers") or []]
    answers = [a for a in answers if a]
    if not MIN_ANSWERS <= len(answers) <= MAX_ANSWERS:
        raise QuestError(f"A poll has {MIN_ANSWERS} to {MAX_ANSWERS} answers.")
    if len({a.lower() for a in answers}) < len(answers):
        raise QuestError("Two answers are the same.")
    try:
        closes = float(wanted["closes_at"])
    except (KeyError, TypeError, ValueError):
        raise QuestError("Pick when the poll closes.") from None
    if not POLL_SHORTEST <= closes - time.time() <= POLL_LONGEST:
        raise QuestError("A poll stays open from 10 minutes to 7 days.")
    return {"question": question, "answers": answers, "closes_at": closes}


class GameEvents:
    def __init__(self, core: AgentCore):
        self.core = core
        self.db = core.db
        self._task: asyncio.Task | None = None
        self._saving: set[int] = set()  # events whose end save was asked for

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="game-events")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("events check failed")
            await asyncio.sleep(EVERY)

    # -- reading ----------------------------------------------------------------
    def recent(self, server_id: str | None = None) -> list[dict[str, Any]]:
        """Events coming up, going on, or ended in the last 8 days, on
        servers that are still there."""
        rows = self.db.query(
            "SELECT * FROM game_events WHERE ends_at > ? "
            + ("AND server_id = ? " if server_id else "")
            + "ORDER BY starts_at, id",
            (time.time() - KEEP, server_id) if server_id else (time.time() - KEEP,),
        )
        return [r for r in rows if r["server_id"] in self.core.servers]

    def get(self, server_id: str, event_id: Any) -> dict[str, Any]:
        found = next((e for e in self.recent(server_id) if str(e["id"]) == str(event_id)), None)
        if found is None:
            raise QuestError("That event isn't there any more.")
        return found

    @staticmethod
    def state(event: dict[str, Any], now: float | None = None) -> str:
        now = now or time.time()
        if event["cancelled_at"]:
            return "cancelled"
        if now < event["starts_at"]:
            return "coming"
        if now < event["ends_at"]:
            return "on"
        if not event["counted_at"]:
            return "counting"
        if event["kind"] == "approval" and not event["approved_at"]:
            return "approving"
        return "ended"

    def view(self, event: dict[str, Any]) -> dict[str, Any]:
        """An event as the apps and the bot show it."""
        rsvps = self.db.query(
            "SELECT discord_id, discord_name, answer FROM event_rsvps WHERE event_id = ? "
            "ORDER BY at",
            (event["id"],),
        )
        attendance = self.db.query(
            "SELECT discord_id, discord_name, minutes, attended, how, fixed_by FROM "
            "event_attendance WHERE event_id = ? ORDER BY attended DESC, minutes DESC, "
            "discord_name COLLATE NOCASE",
            (event["id"],),
        )
        state = self.state(event)
        return {
            **{k: v for k, v in event.items() if k != "posts"},
            "rewards": json.loads(event["rewards"]),
            "start_server": bool(event["start_server"]),
            "state": state,
            "answers": {a: sum(r["answer"] == a for r in rsvps) for a in ANSWERS},
            "going": [r for r in rsvps if r["answer"] == "going"],
            "attendance": [{**a, "attended": bool(a["attended"])} for a in attendance],
            "fixable": bool(event["counted_at"])
            and not event["cancelled_at"]
            and time.time() < event["ends_at"] + FIX_FOR,
        }

    def page(self, server_id: str) -> list[dict[str, Any]]:
        return [self.view(e) for e in self.recent(server_id)]

    # -- making, calling off and answering ------------------------------------------------
    async def make(self, ctx: ServerContext, wanted: dict[str, Any], by: str) -> dict[str, Any]:
        event = check_event(ctx, wanted)
        coming = [e for e in self.recent(ctx.server_id) if self.state(e) in ("coming", "on")]
        if len(coming) >= MAX_COMING:
            raise QuestError(f"{ctx.name} has {MAX_COMING} events coming up already.")
        made = self.db.insert(
            "game_events",
            {
                **event,
                "server_id": ctx.server_id,
                "rewards": json.dumps(event["rewards"]),
                "created_at": time.time(),
                "created_by": by,
            },
        )
        await ctx.bus.publish(
            Event(
                type="event_added",
                message=f"New event on {ctx.name}: {event['title']}",
                data={"event_id": made, "title": event["title"], "by": by},
            )
        )
        return self.get(ctx.server_id, made)

    def cancel(self, server_id: str, event_id: Any) -> dict[str, Any]:
        """Called off before its end; after that it's history."""
        event = self.get(server_id, event_id)
        if not self.db.execute(
            "UPDATE game_events SET cancelled_at = ? WHERE id = ? AND cancelled_at IS NULL "
            "AND ends_at > ?",
            (time.time(), event["id"], time.time()),
        ).rowcount:
            raise QuestError("That event has ended or was called off already.")
        return event

    def rsvp(self, server_id: str, event_id: Any, member: str, name: str, answer: str) -> str:
        event = self.get(server_id, event_id)
        if answer not in ANSWERS:
            raise QuestError("That isn't an answer.")
        if self.state(event) not in ("coming", "on"):
            raise QuestError(f"{event['title']} has ended or was called off.")
        self.db.execute(
            "INSERT INTO event_rsvps (event_id, discord_id, discord_name, answer, at) VALUES "
            "(?, ?, ?, ?, ?) ON CONFLICT(event_id, discord_id) DO UPDATE SET answer = "
            "excluded.answer, discord_name = excluded.discord_name, at = excluded.at",
            (event["id"], member, name[:100], answer, time.time()),
        )
        if answer == "no":
            return f"Got it: you can't come to {event['title']}."
        if not self.core.links.of_member(member):
            return (
                f"Got it: {answer} for {event['title']}. Link your Minecraft account with /link "
                "so your attendance can count."
            )
        return f"Got it: {answer} for {event['title']}."

    # -- the check every 30 seconds ------------------------------------------------------
    async def check(self, now: float | None = None) -> None:
        now = now or time.time()
        for event in self.recent():
            ctx = self.core.servers.get(event["server_id"])
            if ctx is None or event["cancelled_at"] or event["counted_at"]:
                continue
            if (
                event["start_server"]
                and not event["started_at"]
                and event["starts_at"] - START_BEFORE <= now < event["ends_at"]
            ):
                await self._start_server(ctx, event, now)
            if now < event["ends_at"]:
                continue
            if (
                event["kind"] == "advancement"
                and event["id"] not in self._saving
                and ctx.server.state.value == "ONLINE"
            ):
                # The files are written every few minutes; this writes them now.
                self._saving.add(event["id"])
                try:
                    await ctx.server.send_command("save-all", internal=True)
                except Exception as exc:
                    log.info("couldn't ask %s to save: %s", ctx.name, exc)
            if now >= event["ends_at"] + COUNT_AFTER:
                await self.count(ctx, event, now)
        for poll in self.db.query(
            "SELECT * FROM polls WHERE closed_at IS NULL AND closes_at <= ?", (now,)
        ):
            self.db.execute(
                "UPDATE polls SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
                (poll["closes_at"], poll["id"]),
            )

    async def _start_server(self, ctx: ServerContext, event: dict[str, Any], now: float) -> None:
        from .minecraft.process import ServerError

        if not self.db.execute(
            "UPDATE game_events SET started_at = ? WHERE id = ? AND started_at IS NULL",
            (now, event["id"]),
        ).rowcount:
            return
        if ctx.server.state.value not in ("OFFLINE", "CRASHED"):
            return
        if ctx.server.maintenance and ctx.config.maintenance.block_scheduled_tasks:
            result = "Skipped: maintenance mode"
        else:
            try:
                await ctx.server.start(actor="event")
                result = "Started"
            except ServerError as exc:
                result = f"Skipped: {exc}"
        ctx.db.add_event(ctx.server_id, "schedule", f"{result} for the event {event['title']}")

    def _seen(self, ctx: ServerContext, event: dict[str, Any], now: float) -> list[dict]:
        """Linked members who played during the event, with their seconds
        online inside it (a session still going counts up to the end)."""
        end, start = event["ends_at"], event["starts_at"]
        return self.db.query(
            "SELECT l.discord_id, MAX(l.discord_name) AS name, "
            "SUM(MIN(COALESCE(s.left_at, ?), ?) - MAX(s.joined_at, ?)) AS seconds "
            "FROM player_sessions s JOIN players p ON p.server_id = s.server_id "
            "AND p.username = s.username JOIN discord_links l ON l.uuid = p.uuid "
            "WHERE s.server_id = ? AND s.joined_at < ? AND COALESCE(s.left_at, ?) > ? "
            "GROUP BY l.discord_id",
            (now, end, start, ctx.server_id, end, now, start),
        )

    async def _earned(self, ctx: ServerContext, event: dict[str, Any], member: str) -> bool:
        """The advancement was earned inside the event, on any linked account."""
        for account in self.core.quests.accounts(member, ctx.server_id):
            _stats, earned, _saved = await self.core.quests._read(ctx, str(account["uuid"]))
            when = earned_at(earned, event["goal"])
            if when is not None and event["starts_at"] <= when <= event["ends_at"]:
                return True
        return False

    async def count(self, ctx: ServerContext, event: dict[str, Any], now: float) -> None:
        """Who came: measured for advancement and online events (rewards go
        at once), suggested for approval ones (rewards wait for the OK)."""
        if not self.db.execute(
            "UPDATE game_events SET counted_at = ? WHERE id = ? AND counted_at IS NULL",
            (now, event["id"]),
        ).rowcount:
            return
        self._saving.discard(event["id"])
        rows: dict[str, dict[str, Any]] = {}
        for seen in self._seen(ctx, event, now):
            minutes = int(float(seen["seconds"] or 0) // 60)
            if event["kind"] == "advancement":
                came = await self._earned(ctx, event, seen["discord_id"])
            else:
                came = minutes >= ATTEND_MINUTES
            rows[seen["discord_id"]] = {"name": seen["name"], "minutes": minutes, "came": came}
        for going in self.db.query(
            "SELECT discord_id, discord_name FROM event_rsvps WHERE event_id = ? AND answer = "
            "'going'",
            (event["id"],),
        ):
            rows.setdefault(
                going["discord_id"], {"name": going["discord_name"], "minutes": 0, "came": False}
            )
        how = "suggested" if event["kind"] == "approval" else "measured"
        for member, row in rows.items():
            self.db.execute(
                "INSERT OR IGNORE INTO event_attendance (event_id, discord_id, discord_name, "
                "minutes, attended, how) VALUES (?, ?, ?, ?, ?, ?)",
                (event["id"], member, row["name"][:100], row["minutes"], int(row["came"]), how),
            )
            if row["came"] and how == "measured":
                self._give(ctx, event, member)
        total = sum(r["came"] for r in rows.values())
        if how == "suggested":
            await ctx.bus.publish(
                Event(
                    type="event_attendance",
                    level="info",
                    message=f"Who came to {event['title']}? The list waits for your OK",
                    data={"event_id": event["id"], "title": event["title"]},
                )
            )
        else:
            ctx.db.add_event(
                ctx.server_id, "event_counted", f"{total} came to the event {event['title']}"
            )

    # -- rewards, approving and fixing ---------------------------------------------------
    def _give(self, ctx: ServerContext, event: dict[str, Any], member: str) -> None:
        """50 Level XP and the event's rewards, once per member and event."""
        rewards = json.loads(event["rewards"])
        extra = sum(r["amount"] for r in rewards if r["kind"] == "level_xp")
        levels.award(self.db, member, ATTEND_XP + extra, "event", str(event["id"]))
        for reward in rewards:
            if reward["kind"] == "level_xp":
                continue
            self.db.execute(
                "INSERT OR IGNORE INTO quest_rewards (quest_id, discord_id, period, server_id, "
                "kind, value, state, created_at, event_id) VALUES (0, ?, ?, ?, ?, ?, 'waiting', "
                "?, ?)",
                (
                    member,
                    f"event:{event['id']}",
                    ctx.server_id,
                    reward["kind"],
                    json.dumps(reward),
                    time.time(),
                    event["id"],
                ),
            )
        self.core.quests.spawn(self.core.quests.give_waiting(ctx, member))

    def _take(self, event: dict[str, Any], member: str) -> None:
        """Back out of a mistake: the Level XP and anything not given yet.
        Items and Discord roles already given stay (the app says so)."""
        self.db.execute(
            "DELETE FROM level_xp WHERE discord_id = ? AND source = 'event' AND ref = ?",
            (member, str(event["id"])),
        )
        self.db.execute(
            "DELETE FROM quest_rewards WHERE event_id = ? AND discord_id = ? AND state = 'waiting'",
            (event["id"], member),
        )

    def approve(self, ctx: ServerContext, event_id: Any, by: str) -> dict[str, Any]:
        """The suggested list of an approval event is right: rewards go out."""
        event = self.get(ctx.server_id, event_id)
        if event["kind"] != "approval" or not event["counted_at"] or event["cancelled_at"]:
            raise QuestError("That event has no list waiting for approval.")
        if not self.db.execute(
            "UPDATE game_events SET approved_at = ? WHERE id = ? AND approved_at IS NULL",
            (time.time(), event["id"]),
        ).rowcount:
            raise QuestError("That list was approved already.")
        came = self.db.query(
            "SELECT discord_id FROM event_attendance WHERE event_id = ? AND attended = 1",
            (event["id"],),
        )
        for row in came:
            self._give(ctx, event, row["discord_id"])
        ctx.db.audit(
            "event_attendance_approve", user=by, target=event["title"], detail=f"{len(came)} came"
        )
        return event

    def fix(
        self, ctx: ServerContext, event_id: Any, member: str, attended: bool, by: str
    ) -> dict[str, Any]:
        """A moderator corrects one member, up to 7 days after the end."""
        event = self.get(ctx.server_id, event_id)
        if not self.view(event)["fixable"]:
            raise QuestError("Attendance can be fixed for 7 days after an event ends.")
        row = self.db.query_one(
            "SELECT * FROM event_attendance WHERE event_id = ? AND discord_id = ?",
            (event["id"], member),
        )
        if row is None:
            raise QuestError("That member isn't on the list.")
        if bool(row["attended"]) == attended:
            return event
        self.db.execute(
            "UPDATE event_attendance SET attended = ?, how = 'fixed', fixed_by = ?, fixed_at = ? "
            "WHERE event_id = ? AND discord_id = ?",
            (int(attended), by, time.time(), event["id"], member),
        )
        if event["kind"] != "approval" or event["approved_at"]:
            if attended:
                self._give(ctx, event, member)
            else:
                self._take(event, member)
        ctx.db.audit(
            "event_attendance_fix",
            user=by,
            target=event["title"],
            detail=f"Discord member {member} {'came' if attended else 'did not come'}",
        )
        return event

    # -- polls -------------------------------------------------------------------------
    def polls(self, server_id: str | None = None) -> list[dict[str, Any]]:
        """Polls open, or closed in the last 8 days."""
        rows = self.db.query(
            "SELECT * FROM polls WHERE (closed_at IS NULL OR closed_at > ?) "
            + ("AND server_id = ? " if server_id else "")
            + "ORDER BY id",
            (time.time() - KEEP, server_id) if server_id else (time.time() - KEEP,),
        )
        return [r for r in rows if r["server_id"] in self.core.servers]

    def poll(self, server_id: str, poll_id: Any) -> dict[str, Any]:
        found = next((p for p in self.polls(server_id) if str(p["id"]) == str(poll_id)), None)
        if found is None:
            raise QuestError("That poll isn't there any more.")
        return found

    def poll_view(self, poll: dict[str, Any]) -> dict[str, Any]:
        answers = json.loads(poll["answers"])
        counts = [0] * len(answers)
        for row in self.db.query(
            "SELECT answer, COUNT(*) AS n FROM poll_votes WHERE poll_id = ? GROUP BY answer",
            (poll["id"],),
        ):
            if 0 <= row["answer"] < len(counts):
                counts[row["answer"]] = int(row["n"])
        return {
            **{k: v for k, v in poll.items() if k != "posts"},
            "answers": answers,
            "votes": counts,
            "total": sum(counts),
            "open": poll["closed_at"] is None,
        }

    def poll_page(self, server_id: str) -> list[dict[str, Any]]:
        return [self.poll_view(p) for p in self.polls(server_id)]

    async def make_poll(
        self, ctx: ServerContext, wanted: dict[str, Any], by: str
    ) -> dict[str, Any]:
        poll = check_poll(wanted)
        if sum(p["closed_at"] is None for p in self.polls(ctx.server_id)) >= MAX_OPEN_POLLS:
            raise QuestError(f"{ctx.name} has {MAX_OPEN_POLLS} polls open. Close one first.")
        made = self.db.insert(
            "polls",
            {
                **poll,
                "server_id": ctx.server_id,
                "answers": json.dumps(poll["answers"]),
                "created_at": time.time(),
                "created_by": by,
            },
        )
        await ctx.bus.publish(
            Event(
                type="poll_added",
                message=f"New poll on {ctx.name}: {poll['question']}",
                data={"poll_id": made, "question": poll["question"], "by": by},
            )
        )
        return self.poll(ctx.server_id, made)

    def vote(self, server_id: str, poll_id: Any, member: str, answer: Any) -> str:
        poll = self.poll(server_id, poll_id)
        answers = json.loads(poll["answers"])
        try:
            picked = int(answer)
        except (TypeError, ValueError):
            picked = -1
        if not 0 <= picked < len(answers):
            raise QuestError("That isn't an answer.")
        if poll["closed_at"] is not None or poll["closes_at"] <= time.time():
            raise QuestError("That poll has closed.")
        self.db.execute(
            "INSERT INTO poll_votes (poll_id, discord_id, answer, at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(poll_id, discord_id) DO UPDATE SET answer = excluded.answer, at = "
            "excluded.at",
            (poll["id"], member, picked, time.time()),
        )
        return f"Your vote: {answers[picked]}. You can change it until the poll closes."

    def close_poll(self, server_id: str, poll_id: Any) -> dict[str, Any]:
        poll = self.poll(server_id, poll_id)
        if not self.db.execute(
            "UPDATE polls SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
            (time.time(), poll["id"]),
        ).rowcount:
            raise QuestError("That poll has closed already.")
        return poll
