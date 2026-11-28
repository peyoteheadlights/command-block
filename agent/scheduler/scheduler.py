"""Scheduled tasks.

When a schedule runs (its kind and expr):

  daily     expr = "23:00"
  weekly    expr = "sun 04:00"
  interval  expr = "6h" / "90m" / "45s"
  cron      expr = "*/15 * * * *" (minute hour day month weekday)
  once      expr = "2026-12-24 18:00", deleted after it runs
  event     expr = "started" / "stopped" / "crashed" / "first_join" /
            "empty 30" (nobody on for 30 minutes) / "tps 15" (under 15)

Tasks: start, stop, restart, backup, log_cleanup, notify, maintenance_on,
maintenance_off, three ready-made commands, a message to players, a console
command and "steps" (up to 10 tasks in a row). A console command passes the
same checks as one typed in the console (agent/minecraft/commands.py), when
it is saved and again when it runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from ..events import Event, EventBus
from ..minecraft.chat import ChatError, check_message, say
from ..minecraft.commands import CommandError, describe_danger, validate

log = logging.getLogger("msc.scheduler")

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
TIME_RE = re.compile(r"^(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)$")
WEEKLY_RE = re.compile(r"^(?P<day>mon|tue|wed|thu|fri|sat|sun)\s+(?P<time>[0-2]?\d:[0-5]\d)$", re.I)
INTERVAL_RE = re.compile(r"^(?P<n>\d+)(?P<unit>[smhd])$", re.I)

TASKS = (
    "start",
    "stop",
    "restart",
    "backup",
    "log_cleanup",
    "notify",
    "maintenance_on",
    "maintenance_off",
    "weather_clear",
    "time_day",
    "clear_items",
    "message",
    "command",
    "steps",
)
# The ready-made commands: fixed text, written here, so they need no confirmation.
READY_MADE = {
    "weather_clear": "weather clear",
    "time_day": "time set day",
    "clear_items": "kill @e[type=item]",
}
MAX_STEPS = 10
# Players hear a stop or restart again this many seconds before it.
LAST_WARNING = 10
# A start, stop, restart or notice more than this late (the PC was asleep
# or off) is skipped, not run late: a restart at 4 in the morning shouldn't
# kick players at noon. Backups, clean-ups and maintenance still run late.
# The loop looks every 20 seconds, so 5 minutes is "missed".
MISSED_AFTER = 300
# Commands that act on Join protection's own block list from the console
# (agent/api/routes/server.py) and would block every player if sent as-is.
NOT_SCHEDULED = ("ban-ip", "pardon-ip")
SKIPPED_WHEN_LATE = frozenset({"start", "stop", "restart", "notify"})


async def _wait(seconds: float) -> None:
    await asyncio.sleep(seconds)


EVENTS = {
    "server_started": "started",
    "server_stopped": "stopped",
    "server_crashed": "crashed",
}
EVENT_RE = re.compile(r"^(?:started|stopped|crashed|first_join|(?:empty|tps) (?P<n>\d{1,4}))$")
CRON_FIELDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


class ScheduleError(ValueError):
    pass


class NeedsConfirmation(ScheduleError):
    """A command the console asks "are you sure?" about: the apps ask, then
    save again with "confirm" on that step."""


def _cron_field(text: str, low: int, high: int) -> set[int]:
    """One cron field: *, 5, 1-5, 1,3,5, */15 or 1-30/5."""
    values: set[int] = set()
    for part in text.split(","):
        rng, _, step_text = part.partition("/")
        step = int(step_text) if step_text.isdigit() else (1 if not step_text else 0)
        if rng == "*":
            start, end = low, high
        elif "-" in rng:
            a, _, b = rng.partition("-")
            start, end = (int(a), int(b)) if a.isdigit() and b.isdigit() else (-1, -1)
        else:
            start = int(rng) if rng.isdigit() else -1
            end = high if step_text else start
        if step < 1 or not low <= start <= end <= high:
            raise ScheduleError(
                "A cron line is 5 parts: minute hour day month weekday, like */15 * * * *"
            )
        values.update(range(start, end + 1, step))
    return values


def cron_next(expr: str, now: datetime) -> datetime:
    parts = expr.split()
    if len(parts) != 5:
        raise ScheduleError(
            "A cron line is 5 parts: minute hour day month weekday, like */15 * * * *"
        )
    minutes, hours, days, months, weekdays = (
        _cron_field(part, *bounds) for part, bounds in zip(parts, CRON_FIELDS, strict=True)
    )
    weekdays = {d % 7 for d in weekdays}  # 0 and 7 are both Sunday
    # As in cron: when both day and weekday are limited, either one matching is enough.
    either = parts[2] != "*" and parts[4] != "*"
    start = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.replace(hour=0, minute=0)
    for _ in range(366 * 5):  # 5 years covers every 29 February
        on_day = day.day in days
        on_weekday = (day.weekday() + 1) % 7 in weekdays
        if day.month in months and ((on_day or on_weekday) if either else on_day and on_weekday):
            for hour in sorted(hours):
                for minute in sorted(minutes):
                    when = day.replace(hour=hour, minute=minute)
                    if when >= start:
                        return when
        day += timedelta(days=1)
    raise ScheduleError("That cron line never runs.")


def next_run(kind: str, expr: str, after: float | None = None) -> float | None:
    """When a schedule runs next; None for one that waits for an event."""
    now = datetime.fromtimestamp(after or time.time())
    kind = kind.lower()
    expr = " ".join(expr.strip().lower().split())

    if kind == "event":
        if not EVENT_RE.match(expr):
            raise ScheduleError(
                "Pick what it waits for: started, stopped, crashed, first_join, "
                "empty 30 (minutes) or tps 15."
            )
        return None

    if kind == "cron":
        return cron_next(expr, now).timestamp()

    if kind == "once":
        try:
            target = datetime.strptime(expr, "%Y-%m-%d %H:%M")
        except ValueError:
            raise ScheduleError("A one-off needs a date and time like 2026-12-24 18:00") from None
        if target <= now:
            raise ScheduleError("That time has already passed.")
        return target.timestamp()

    if kind == "daily":
        m = TIME_RE.match(expr)
        if not m:
            raise ScheduleError("A daily schedule needs a time like 23:00")
        target = now.replace(
            hour=int(m.group("h")), minute=int(m.group("m")), second=0, microsecond=0
        )
        if target <= now:
            target += timedelta(days=1)
        return target.timestamp()

    if kind == "weekly":
        m = WEEKLY_RE.match(expr)
        if not m:
            raise ScheduleError("A weekly schedule needs a day and time like 'sun 04:00'")
        wanted = WEEKDAYS[m.group("day").lower()]
        hh, mm = m.group("time").split(":")
        target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        days_ahead = (wanted - target.weekday()) % 7
        target += timedelta(days=days_ahead)
        if target <= now:
            target += timedelta(days=7)
        return target.timestamp()

    if kind == "interval":
        m = INTERVAL_RE.match(expr)
        if not m:
            raise ScheduleError("An interval needs a value like 6h, 90m or 45s")
        seconds = (
            int(m.group("n")) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group("unit").lower()]
        )
        if seconds < 30:
            raise ScheduleError("The shortest gap between runs is 30 seconds.")
        return (now + timedelta(seconds=seconds)).timestamp()

    raise ScheduleError("Pick every day, every week, or every few hours.")


def steps_of(task: str, payload: dict) -> list[tuple[str, dict]]:
    """The tasks a schedule runs: its steps, or itself."""
    if task != "steps":
        return [(task, payload)]
    steps = payload.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ScheduleError(f"Steps in a row need 1 to {MAX_STEPS} steps.")
    if not all(isinstance(step, dict) for step in steps):
        raise ScheduleError("A step has to say what it does.")
    return [(str(step.get("task")), step) for step in steps]


def check_task(task: str, payload: dict) -> None:
    """Refuse a schedule that couldn't run, before it is saved."""
    if len(json.dumps(payload)) > 8000:
        raise ScheduleError("That schedule is too long.")
    for name, step in steps_of(task, payload):
        if name not in TASKS or (name == "steps" and task == "steps"):
            raise ScheduleError(
                f"'{name}' isn't something this app can schedule. Pick one of: {', '.join(TASKS)}."
            )
        try:
            if name == "message":
                check_message(step.get("text") or "")
            elif name == "command":
                danger = describe_danger(step.get("command") or "")
                if danger and not step.get("confirm"):
                    raise NeedsConfirmation(danger)
                checked_command(step)
            elif name == "backup":
                for key in ("before", "after"):
                    if step.get(key):
                        check_message(step[key])
        except (ChatError, CommandError) as exc:
            raise ScheduleError(str(exc)) from None


def check_loop(kind: str, expr: str, task: str, payload: dict) -> None:
    """Refuse a schedule that would set itself off again and again."""
    if kind.lower() != "event":
        return
    tasks = {name for name, _ in steps_of(task, payload)}
    event = expr.strip().lower()
    if (event == "started" and "restart" in tasks) or (
        event == "stopped" and tasks & {"start", "restart"}
    ):
        raise ScheduleError("That would run again every time it ran, so it can't be saved.")


def checked_command(step: dict) -> str:
    """The step's console command, through the console's own checks."""
    validated = validate(step.get("command") or "", confirm=bool(step.get("confirm")))
    if validated.name in NOT_SCHEDULED:
        raise CommandError(f"'{validated.name}' can't be scheduled. Use the console for it.")
    return validated.raw


class Scheduler:
    def __init__(
        self, config, bus: EventBus, db, server, backups=None, notifier=None, players=None
    ):
        self.config = config
        self.players = players
        # Event schedules already run for the spell going on now (empty or
        # slow), by id, so each runs once per spell; and when "empty" began.
        self._fired: set[int] = set()
        self._empty_since: float | None = None
        self._running: set[asyncio.Task] = set()
        self.bus = bus
        self.db = db
        self.server = server
        self.backups = backups
        self.notifier = notifier
        # The agent's one maintenance switch (saved to the config); set by core.
        self.set_maintenance: Callable[..., Any] | None = None
        self._task: asyncio.Task | None = None
        self.handlers: dict[str, Callable] = {
            "start": self._task_start,
            "stop": self._task_stop,
            "restart": self._task_restart,
            "backup": self._task_backup,
            "log_cleanup": self._task_log_cleanup,
            "notify": self._task_notify,
            "maintenance_on": self._task_maintenance_on,
            "maintenance_off": self._task_maintenance_off,
            "weather_clear": self._task_ready_made,
            "time_day": self._task_ready_made,
            "clear_items": self._task_ready_made,
            "message": self._task_message,
            "command": self._task_command,
            "steps": self._task_steps,
        }

    # ------------------------------------------------------------------
    def list_schedules(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM schedules WHERE server_id = ? ORDER BY next_run ASC",
            (self.server.server_id,),
        )
        for row in rows:
            row["payload"] = json.loads(row.get("payload") or "{}")
            row["enabled"] = bool(row["enabled"])
        return rows

    def add(
        self,
        name: str,
        task: str,
        kind: str,
        expr: str,
        payload: dict | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        if task not in TASKS:
            raise ScheduleError(
                f"'{task}' isn't something this app can schedule. Pick one of: {', '.join(TASKS)}."
            )
        check_task(task, payload or {})
        check_loop(kind, expr, task, payload or {})
        upcoming = next_run(kind, expr)
        row_id = self.db.insert(
            "schedules",
            {
                "server_id": self.server.server_id,
                "name": name[:80],
                "task": task,
                "kind": kind.lower(),
                "expr": expr.strip(),
                "payload": json.dumps(payload or {}),
                "enabled": 1 if enabled else 0,
                "next_run": upcoming,
            },
        )
        return self.get(row_id)

    def get(self, schedule_id: int) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM schedules WHERE id = ? AND server_id = ?",
            (schedule_id, self.server.server_id),
        )
        if not row:
            raise ScheduleError("That schedule isn't on the list any more.")
        row["payload"] = json.loads(row.get("payload") or "{}")
        row["enabled"] = bool(row["enabled"])
        return row

    def update(self, schedule_id: int, **changes) -> dict[str, Any]:
        row = self.get(schedule_id)
        kind = changes.get("kind", row["kind"])
        expr = changes.get("expr", row["expr"])
        task = changes.get("task", row["task"])
        if task not in TASKS:
            raise ScheduleError(f"'{task}' isn't something this app can schedule.")
        check_task(task, changes.get("payload", row["payload"]))
        check_loop(kind, expr, task, changes.get("payload", row["payload"]))
        upcoming = next_run(kind, expr)
        self._fired.discard(schedule_id)
        enabled = changes.get("enabled", row["enabled"])
        self.db.execute(
            "UPDATE schedules SET name = ?, task = ?, kind = ?, expr = ?, payload = ?, "
            "enabled = ?, next_run = ? WHERE id = ?",
            (
                changes.get("name", row["name"]),
                task,
                kind,
                expr,
                json.dumps(changes.get("payload", row["payload"])),
                1 if enabled else 0,
                upcoming,
                schedule_id,
            ),
        )
        return self.get(schedule_id)

    def delete(self, schedule_id: int) -> None:
        self.get(schedule_id)
        self.db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))

    def seed_defaults(self) -> None:
        if self.config.server.test:  # a Test server gets no schedules by default
            return
        if self.db.query_one(
            "SELECT id FROM schedules WHERE server_id = ?", (self.server.server_id,)
        ):
            return
        self.add("Nightly backup", "backup", "daily", "23:00", enabled=True)
        self.add("Weekly restart", "restart", "weekly", "sun 04:00", enabled=False)
        self.add("Log cleanup", "log_cleanup", "weekly", "mon 05:00", enabled=True)

    # ------------------------------------------------------------------
    async def _task_start(self, payload: dict) -> str:
        from ..minecraft.process import ServerError

        try:
            await self.server.start(actor="scheduler")
            return "Server start requested"
        except ServerError as exc:
            return f"Skipped: {exc}"

    async def _task_stop(self, payload: dict) -> str:
        from ..minecraft.process import ServerError
        from ..minecraft.state import ExitReason

        if not await self._warn("stopping", payload.get("warn_seconds", 60)):
            return "Skipped: the server stopped during the warning"
        try:
            await self.server.stop(actor="scheduler", reason=ExitReason.SCHEDULED_STOP)
            return "Server stopped"
        except ServerError as exc:
            return f"Skipped: {exc}"

    async def _task_restart(self, payload: dict) -> str:
        from ..minecraft.process import ServerError

        if not self.server.running:
            # A restart never starts a server someone stopped.
            return "Skipped: the server isn't running"
        if not await self._warn("restarting", payload.get("warn_seconds", 60)):
            return "Skipped: the server stopped during the warning"
        try:
            result = await self.server.restart(actor="scheduler")
        except ServerError as exc:
            return f"Skipped: {exc}"
        return "Server restarted" if result.get("restarted") else f"Failed: {result.get('error')}"

    async def _warn(self, doing: str, warn: Any) -> bool:
        """Tell players, wait, and tell them again 10 seconds before. False
        when the server stopped meanwhile (someone pressed Stop)."""
        from ..minecraft.process import ServerError

        seconds = int(warn or 0)
        if self.server.state.value != "ONLINE" or seconds <= 0:
            return True
        steps = [seconds - LAST_WARNING, LAST_WARNING] if seconds > LAST_WARNING else [seconds]
        left = seconds
        for wait in steps:
            if self.server.state.value != "ONLINE":
                return False
            with contextlib.suppress(ServerError):
                await self.server.send_command(
                    f"say Server {doing} in {left} seconds", internal=True
                )
            await _wait(wait)
            left -= wait
        return self.server.state.value == "ONLINE"

    async def _task_backup(self, payload: dict) -> str:
        if not self.backups:
            return "No backup manager is available"
        await self._tell_players(payload.get("before"))
        result = await self.backups.create(kind=payload.get("kind", "scheduled"), user="scheduler")
        await self._tell_players(payload.get("after"))
        return f"Created {result['name']} ({result['size_bytes'] / 1024**3:.2f} GB)"

    async def _tell_players(self, text: str | None) -> None:
        """A message before or after a backup; skipped while the server is off."""
        if text and self.server.state.value == "ONLINE":
            try:
                await say(self.server, text)
            except Exception:
                log.warning("could not send the backup message", exc_info=True)

    async def _task_log_cleanup(self, payload: dict) -> str:
        days = int(payload.get("days", 30))
        cutoff = time.time() - days * 86400
        removed = 0
        for folder in (self.config.server_dir / "logs", self.config.log_dir):
            if not folder.is_dir():
                continue
            for file in folder.iterdir():
                if not file.is_file() or file.is_symlink():
                    continue
                if file.name in ("latest.log", "agent.log"):
                    continue
                if file.suffix.lower() not in (".gz", ".log", ".txt"):
                    continue
                if file.stat().st_mtime < cutoff:
                    file.unlink(missing_ok=True)
                    removed += 1
        self.db.prune(metrics_days=self.config.monitor.history_days)
        from ..cleanup import empty_old_batches

        empty_old_batches(self.config)
        return f"Removed {removed} old log file(s) and pruned old metrics"

    async def _task_notify(self, payload: dict) -> str:
        # Always a notice: a schedule never publishes a real server event
        # ("server_started" would close start requests).
        await self.bus.publish(
            Event(
                type="maintenance_mode",
                message=payload.get("message", "Scheduled notification"),
                level="info",
            )
        )
        return "Notification published"

    async def _task_ready_made(self, payload: dict) -> str:
        command = READY_MADE[payload["task"]]
        await self.server.send_command(validate(command, confirm=True).raw)
        return f"Sent: {command}"

    async def _task_message(self, payload: dict) -> str:
        return f"Said: {await say(self.server, payload.get('text') or '')}"

    async def _task_command(self, payload: dict) -> str:
        command = checked_command(
            payload
        )  # checked again: the rules may be newer than the schedule
        await self.server.send_command(command)
        return f"Sent: {command}"

    async def _task_steps(self, payload: dict) -> str:
        said = []
        for number, (task, step) in enumerate(steps_of("steps", payload), 1):
            try:
                said.append(f"{number}. {await self.handlers[task]({**step, 'task': task})}")
            except Exception as exc:
                said.append(f"{number}. Failed: {exc}")
                if not payload.get("keep_going"):
                    break
        return " ".join(said)

    async def _task_maintenance_on(self, payload: dict) -> str:
        return self._maintenance(True)

    async def _task_maintenance_off(self, payload: dict) -> str:
        return self._maintenance(False)

    def _maintenance(self, enabled: bool) -> str:
        if self.set_maintenance is None:  # pragma: no cover - set by ServerContext
            self.server.maintenance = enabled
        else:
            self.set_maintenance(enabled, user="scheduler")
        return f"Maintenance mode {'on' if enabled else 'off'}"

    # ------------------------------------------------------------------
    async def run_task(self, row: dict[str, Any]) -> str:
        handler = self.handlers.get(row["task"])
        if not handler:
            return f"Unknown task '{row['task']}'"
        return await handler({**(row.get("payload") or {}), "task": row["task"]})

    def _skip_reason(self, row: dict[str, Any]) -> str | None:
        """Why a due schedule doesn't run now, or None."""
        payload = row.get("payload") or {}
        if (
            self.server.maintenance
            and self.config.maintenance.block_scheduled_tasks
            and row["task"] not in ("maintenance_off", "notify")
        ):
            return "Skipped: maintenance mode"
        if payload.get("only_if_running") and self.server.state.value != "ONLINE":
            return "Skipped: the server isn't running"
        # An unknown count (None) is not "empty": the schedule runs.
        if payload.get("skip_when_empty") and self.players and self.players.online_count() == 0:
            return "Skipped: nobody is on"
        return None

    @staticmethod
    def _missed(row: dict[str, Any], now: float) -> bool:
        """A start, stop, restart or notice that should have run a while ago
        (the PC was asleep or the agent off)."""
        due = row.get("next_run")
        try:
            tasks = {task for task, _ in steps_of(row["task"], row.get("payload") or {})}
        except ScheduleError:
            return False  # a broken row is the run's business; it says so there
        return bool(
            row["kind"] != "event"
            and due
            and now - due > MISSED_AFTER
            and tasks & SKIPPED_WHEN_LATE
        )

    async def _run_row(self, row: dict[str, Any], now: float) -> dict[str, Any]:
        """Run one schedule that is due (or whose event happened) and record it."""
        if self._missed(row, now):
            return self._skip_missed(row, now)
        skipped = self._skip_reason(row)
        if skipped is None and row["kind"] not in ("once", "event"):
            # Saved before the task: a run cut off by a shutdown (during a
            # restart's warning, say) isn't run again at the next start.
            self.db.execute(
                "UPDATE schedules SET next_run = ? WHERE id = ?",
                (next_run(row["kind"], row["expr"], now), row["id"]),
            )
        if skipped is None:
            await self.bus.publish(
                Event(type="schedule_running", message=f"Running scheduled task: {row['name']}")
            )
            try:
                result = await self.run_task(row)
                level = "info"
            except Exception as exc:
                log.exception("scheduled task failed: %s", row["name"])
                result, level = f"Failed: {exc}", "error"
        else:
            result, level = skipped, "info"
        if row["kind"] == "once" and skipped is None:
            self.db.execute("DELETE FROM schedules WHERE id = ?", (row["id"],))
        elif row["kind"] == "once":  # waits until it may run, then runs
            self.db.execute(
                "UPDATE schedules SET last_result = ? WHERE id = ?", (result[:300], row["id"])
            )
            return {"id": row["id"], "name": row["name"], "result": result}
        else:
            self.db.execute(
                "UPDATE schedules SET last_run = ?, next_run = ?, last_result = ? WHERE id = ?",
                (
                    now if skipped is None else row.get("last_run"),
                    next_run(row["kind"], row["expr"], now),
                    result[:300],
                    row["id"],
                ),
            )
        if skipped is None:
            self.db.add_event(
                self.server.server_id, "schedule", f"{row['name']}: {result}", level=level
            )
            await self.bus.publish(
                Event(type="schedule_finished", level=level, message=f"{row['name']}: {result}")
            )
        return {"id": row["id"], "name": row["name"], "result": result}

    def _skip_missed(self, row: dict[str, Any], now: float) -> dict[str, Any]:
        late = (now - row["next_run"]) / 60
        result = f"Skipped: it was missed while the PC was asleep or off ({late:.0f} minutes late)"
        if row["kind"] == "once":
            self.db.execute("DELETE FROM schedules WHERE id = ?", (row["id"],))
        else:
            self.db.execute(
                "UPDATE schedules SET next_run = ?, last_result = ? WHERE id = ?",
                (next_run(row["kind"], row["expr"], now), result, row["id"]),
            )
        self.db.add_event(self.server.server_id, "schedule", f"{row['name']}: {result}")
        return {"id": row["id"], "name": row["name"], "result": result}

    async def run_due(self, now: float | None = None) -> list[dict[str, Any]]:
        now = now or time.time()
        results = []
        for row in self.list_schedules():
            if row["enabled"] and row.get("next_run") and row["next_run"] <= now:
                results.append(await self._run_row(row, now))
        results += await self.run_watched(now)
        return results

    def _events(self, wanted) -> list[dict[str, Any]]:
        return [
            row
            for row in self.list_schedules()
            if row["enabled"]
            and row["kind"] == "event"
            and wanted(" ".join(row["expr"].lower().split()))
        ]

    async def run_watched(self, now: float) -> list[dict[str, Any]]:
        """Event schedules the loop measures: nobody on for N minutes, game
        speed under N. Each runs once per spell and again after it ends."""
        online = self.server.state.value == "ONLINE"
        count = self.players.online_count() if self.players and online else None
        if count == 0:
            self._empty_since = self._empty_since or now
        else:
            self._empty_since = None
        tps = self.server.tps if online else None
        results = []
        for row in self._events(lambda e: e.startswith(("empty ", "tps "))):
            what, n = row["expr"].lower().split()
            if what == "empty":
                happening = self._empty_since is not None and now - self._empty_since >= int(n) * 60
            else:
                happening = tps is not None and tps < int(n)
            if not happening:
                self._fired.discard(row["id"])
            elif row["id"] not in self._fired:
                self._fired.add(row["id"])
                results.append(await self._run_row(row, now))
        return results

    async def heard(self, event: Event) -> None:
        """This server's events: run the schedules waiting for them. Each runs
        in its own task, so a restart never waits inside the event bus."""
        name = EVENTS.get(event.type)
        if event.type == "player_joined" and event.data.get("first"):
            name = "first_join"
        if not name:
            return
        for row in self._events(lambda e: e == name):
            task = asyncio.create_task(self._run_row(row, time.time()))
            self._running.add(task)
            task.add_done_callback(self._running.discard)

    async def run(self) -> None:
        while True:
            try:
                await self.run_due()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                log.exception("scheduler loop error")
            await asyncio.sleep(20)

    def start(self) -> None:
        if not self._task:
            self._task = asyncio.create_task(self.run(), name="scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
