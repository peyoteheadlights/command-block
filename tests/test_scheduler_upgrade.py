# ruff: noqa: F811  (the fixtures imported from other tests are used by name)
"""The bigger scheduler: console commands and messages, steps in a row,
cron lines, one-off schedules, messages around a backup and event triggers.
A stand-in server records what was typed into its console."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

from agent.events import Event
from agent.minecraft.process import ServerError
from agent.scheduler.scheduler import (
    NeedsConfirmation,
    ScheduleError,
    check_task,
    cron_next,
    next_run,
)
from agent.security import permissions as P

from .test_helpers import HELPER_PASSWORD, add_helper, sign_in
from .test_multi_server import wait_for
from .test_use_case_extras import core  # noqa: F401


class Console:
    """The bits of MinecraftServer the scheduler uses."""

    def __init__(self, server_id):
        self.server_id = server_id
        self.sent: list[str] = []
        self.online = True
        self.tps: float | None = 20.0
        self.maintenance = False
        self.restarts = 0

    @property
    def running(self):
        return self.online

    @property
    def state(self):
        class State:
            value = "ONLINE" if self.online else "OFFLINE"

        return State()

    async def send_command(self, command, internal=False):
        if not self.online:
            raise ServerError("The server isn't running.")
        self.sent.append(command)

    async def restart(self, actor=None):
        self.restarts += 1


class Count:
    def __init__(self, n):
        self.n = n

    def online_count(self):
        return self.n


@pytest.fixture
def sched(core):
    scheduler = core.servers["survival"].scheduler
    core.db.execute("DELETE FROM schedules")
    scheduler.server = Console("survival")
    scheduler.players = Count(2)
    return scheduler


# ---------------------------------------------------------------- when
def test_cron_lines():
    now = datetime(2026, 11, 19, 10, 7)
    assert cron_next("*/15 * * * *", now) == datetime(2026, 11, 19, 10, 15)
    assert cron_next("0 4 * * 0", now) == datetime(2026, 11, 22, 4, 0)  # Sunday
    assert cron_next("0 4 * * 7", now) == datetime(2026, 11, 22, 4, 0)
    assert cron_next("30 1-3/2 1 1 *", now) == datetime(2027, 1, 1, 1, 30)
    assert cron_next("0 0 29 2 *", now) == datetime(2028, 2, 29, 0, 0)
    # Day and weekday both limited: either one is enough, as in cron.
    assert cron_next("0 12 25 * 1", now) == datetime(2026, 11, 23, 12, 0)


@pytest.mark.parametrize(
    "expr", ["* * * *", "61 * * * *", "*/0 * * * *", "a * * * *", "5-1 * * * *", "0 0 31 2 *"]
)
def test_bad_cron_lines_are_refused(expr):
    with pytest.raises(ScheduleError):
        cron_next(expr, datetime(2026, 11, 19))


def test_one_off_and_event_kinds():
    assert next_run("once", "2099-01-01 18:00") == datetime(2099, 1, 1, 18).timestamp()
    with pytest.raises(ScheduleError, match="passed"):
        next_run("once", "2020-01-01 18:00")
    for event in ("started", "stopped", "crashed", "first_join", "empty 30", "TPS  15"):
        assert next_run("event", event) is None
    for event in ("joined", "empty", "tps -1", "empty 30m"):
        with pytest.raises(ScheduleError):
            next_run("event", event)


# ---------------------------------------------------------------- what
def test_commands_pass_the_consoles_checks_when_saved():
    check_task("command", {"command": "time set day"})
    with pytest.raises(NeedsConfirmation, match="kills entities"):
        check_task("command", {"command": "kill @a"})
    check_task("command", {"command": "kill @a", "confirm": True})
    for bad in ("say hi; del C:", "ban-ip 1.2.3.4", ""):
        with pytest.raises(ScheduleError):
            check_task("command", {"command": bad, "confirm": True})
    with pytest.raises(ScheduleError):
        check_task("message", {"text": ""})
    with pytest.raises(ScheduleError):
        check_task("backup", {"before": "ㅤ"})  # a blank-looking message


def test_steps_are_limited():
    step = {"task": "message", "text": "hi"}
    check_task("steps", {"steps": [step] * 10})
    for steps in ([], [step] * 11, ["message"], [{"task": "steps", "steps": [step]}]):
        with pytest.raises(ScheduleError):
            check_task("steps", {"steps": steps})


def test_a_schedule_that_would_set_itself_off_is_refused(sched):
    with pytest.raises(ScheduleError, match="every time"):
        sched.add("Loop", "restart", "event", "started")
    with pytest.raises(ScheduleError, match="every time"):
        sched.add("Loop", "steps", "event", "stopped", {"steps": [{"task": "start"}]})
    sched.add("Fine", "restart", "event", "empty 30")


async def test_steps_run_in_a_row_and_stop_at_a_failure(sched):
    steps = [
        {"task": "message", "text": "Clearing items"},
        {"task": "clear_items"},
        {"task": "command", "command": "nosuch;thing"},
        {"task": "weather_clear"},
    ]
    sched.db.insert(  # saved before the rules got stricter: checked again when it runs
        "schedules",
        {
            "server_id": "survival",
            "name": "Tidy",
            "task": "steps",
            "kind": "daily",
            "expr": "04:00",
            "payload": json.dumps({"steps": steps}),
            "enabled": 1,
            "next_run": 1,
        },
    )
    [ran] = await sched.run_due()
    assert sched.server.sent == ["say Clearing items", "kill @e[type=item]"]
    assert "3. Failed" in ran["result"] and "4." not in ran["result"]

    row = sched.list_schedules()[0]
    sched.update(row["id"], payload={"steps": steps[:2] + steps[3:], "keep_going": True})
    sched.server.sent.clear()
    sched.server.online = False
    result = await sched.run_task(sched.get(row["id"]))
    assert result.startswith("1. Failed") and "3. Failed" in result  # kept going
    assert sched.server.sent == []


async def test_only_if_running_and_skip_when_empty(sched):
    row = sched.add("Day", "time_day", "interval", "1h", {"only_if_running": True})
    sched.db.execute("UPDATE schedules SET next_run = 1")
    sched.server.online = False
    [ran] = await sched.run_due()
    assert ran["result"] == "Skipped: the server isn't running"
    assert sched.get(row["id"])["next_run"] > 1  # waits for the next time

    sched.update(row["id"], payload={"skip_when_empty": True})
    sched.db.execute("UPDATE schedules SET next_run = 1")
    sched.server.online, sched.players.n = True, 0
    assert (await sched.run_due())[0]["result"] == "Skipped: nobody is on"
    sched.players.n = None  # not known: never guessed as empty
    sched.db.execute("UPDATE schedules SET next_run = 1")
    assert (await sched.run_due())[0]["result"] == "Sent: time set day"


async def test_a_one_off_deletes_itself_after_it_ran(sched):
    sched.add("Party", "message", "once", "2099-01-01 18:00", {"text": "Party time"})
    sched.db.execute("UPDATE schedules SET next_run = 1")
    await sched.run_due()
    assert sched.server.sent == ["say Party time"]
    assert sched.list_schedules() == []


async def test_messages_before_and_after_a_backup(sched):
    class Backups:
        async def create(self, kind, user):
            sched.server.sent.append("<backup>")
            return {"name": "b1", "size_bytes": 0}

    sched.backups = Backups()
    payload = {"before": "Backing up", "after": "Backup done", "task": "backup"}
    await sched._task_backup(payload)
    assert sched.server.sent == ["say Backing up", "<backup>", "say Backup done"]


# ---------------------------------------------------------------- events
async def test_event_triggers_run_once_per_happening(sched):
    sched.add("Welcome", "message", "event", "first_join", {"text": "Welcome!"})
    sched.add("Crash note", "message", "event", "crashed", {"text": "Sorry"})
    await sched.heard(Event(type="player_joined", data={"first": False}))
    await sched.heard(Event(type="player_joined", data={"first": True}))
    assert await wait_for(lambda: sched.server.sent == ["say Welcome!"], 3)

    sched.add("Empty restart", "restart", "event", "empty 30")
    sched.add("Slow", "message", "event", "tps 15", {"text": "Lagging"})
    sched.players.n, sched.server.tps = 0, 12.0
    await sched.run_due(now=1000)
    await sched.run_due(now=1000 + 29 * 60)
    assert sched.server.restarts == 0 and sched.server.sent[-1] == "say Lagging"
    await sched.run_due(now=1000 + 30 * 60)
    await sched.run_due(now=1000 + 40 * 60)
    assert sched.server.restarts == 1 and sched.server.sent.count("say Lagging") == 1
    sched.players.n, sched.server.tps = 1, 20.0  # the spell ends ...
    await sched.run_due(now=5000)
    sched.players.n, sched.server.tps = 0, 10.0  # ... and a new one starts again
    await sched.run_due(now=6000)
    await sched.run_due(now=6000 + 30 * 60)
    assert sched.server.restarts == 2 and sched.server.sent.count("say Lagging") == 2
    await asyncio.sleep(0)


async def test_a_new_player_is_marked_first(core):
    seen = []
    core.bus.subscribe(lambda e: seen.append(e) if e.type == "player_joined" else None)
    players = core.servers["survival"].players
    await players.player_joined("Alex")
    await players.player_left("Alex")
    await players.player_joined("Alex")
    assert [e.data["first"] for e in seen] == [True, False]


# ---------------------------------------------------------------- who
def test_a_scheduled_command_needs_console_rights_too(multi_client):
    client = multi_client
    owner = dict(client.headers)
    client.headers.pop("Authorization")
    add_helper(client, owner)
    body = {"grants": {"survival": sorted(P.RUN_IT - {P.CONSOLE_SEND})}}
    assert client.put("/api/accounts/sam", json=body, headers=owner).status_code == 200
    sam = sign_in(client, "sam", HELPER_PASSWORD)
    command = {"name": "Day", "task": "command", "kind": "daily", "expr": "04:00"}
    command["payload"] = {"command": "time set day"}
    steps = {
        **command,
        "task": "steps",
        "payload": {"steps": [{**command["payload"], "task": "command"}]},
    }
    for schedule in (command, steps):
        assert client.post("/api/schedules", json=schedule, headers=sam).status_code == 403
        assert client.post("/api/schedules", json=schedule, headers=owner).status_code == 200
    ready = {**command, "task": "time_day", "payload": {}}
    assert client.post("/api/schedules", json=ready, headers=sam).status_code == 200
    bad = {**command, "payload": {"command": "kill @a"}}
    refused = client.post("/api/schedules", json=bad, headers=owner)
    assert refused.status_code == 409 and "kills entities" in refused.json()["detail"]
