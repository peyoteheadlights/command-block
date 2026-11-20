"""Less downtime: the agent stops and starts its servers together, a slow
start is only an alert, scheduled restarts warn again 10 seconds before and
are skipped when missed, changes say what waits for a restart, and
"Restart when nobody's on"."""

from __future__ import annotations

import asyncio
import time

import pytest

from agent import downtime, gamesettings
from agent.core import AgentCore
from agent.database.db import Database
from agent.events import EventBus
from agent.minecraft.process import MinecraftServer, ServerError
from agent.minecraft.state import ServerState
from agent.scheduler import scheduler as sched
from agent.scheduler.scheduler import Scheduler

from .conftest import build_multi_config
from .test_multi_server import stop_core, wait_for


async def started(tmp_path, **overrides):
    core = AgentCore(build_multi_config(tmp_path, **overrides))
    seen = []
    core.bus.subscribe(lambda e: seen.append(e))
    ctx = core.servers["survival"]
    await ctx.start()
    await ctx.server.start()
    assert await ctx.server.wait_online(20)
    return core, ctx, seen


async def finish(core):
    for ctx in core.servers.values():
        if ctx.server.running:
            await ctx.server.stop()
        await ctx.stop()
    await core.jobs.stop()
    core.db.close()


def said(ctx) -> list[str]:
    return [line.raw for line in ctx.server.console.tail(200)]


# ------------------------------------------------------- "Starting too long"
def test_the_start_limit_is_twice_the_last_measured_start(config):
    db = Database(config.database_path)
    db.register_server("test", "Test", str(config.server_dir))
    server = MinecraftServer(config, EventBus(), db)
    config.set("server.start_timeout", 300)
    # Never measured: the setting.
    assert server.start_limit() == 300
    db.set_setting("startup_seconds", 42.5)
    assert server.start_limit() == 85
    db.close()


async def test_a_slow_start_is_only_an_alert(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_BOOT_DELAY", "1.5")
    cfg = build_multi_config(tmp_path)
    cfg.set_server_value("survival", "server.start_timeout", 0.3)
    core = AgentCore(cfg)
    seen = []
    core.bus.subscribe(lambda e: seen.append(e))
    ctx = core.servers["survival"]
    await ctx.start()
    try:
        await ctx.server.start()
        assert await wait_for(lambda: any(e.type == "start_slow" for e in seen), 10)
        slow = next(e for e in seen if e.type == "start_slow")
        assert slow.level == "warn" and slow.data["limit_seconds"] == 0
        # Never stopped for it: it carries on and comes online.
        assert await ctx.server.wait_online(20)
        # Measured now: next time the limit is twice this start.
        measured = ctx.db.get_setting("startup_seconds")
        assert measured and ctx.server.start_limit() == 2 * measured
    finally:
        await finish(core)


async def test_a_normal_start_sends_no_slow_alert(tmp_path):
    core, ctx, seen = await started(tmp_path)
    try:
        await asyncio.sleep(0.3)
        assert not any(e.type == "start_slow" for e in seen)
    finally:
        await finish(core)


# ------------------------------------------------- waiting for a restart
async def test_what_waits_for_a_restart(tmp_path):
    core, ctx, _seen = await started(tmp_path)
    try:
        assert downtime.waiting(ctx) == []

        async def nothing():
            return None

        await ctx.mod_change("Adding a mod", nothing)
        assert downtime.waiting(ctx) == ["mods"]
        # As a memory change does: the command the next start uses is
        # different. (The fake server's command is a fixed one.)
        ctx.config.set("server.raw_command", [*ctx.config.server.raw_command, "--more"])
        assert downtime.waiting(ctx) == ["memory", "mods"]
        await gamesettings.save(ctx, updates={"max-players": "7"})
        assert downtime.waiting(ctx) == ["memory", "mods", "settings"]
        assert ctx.status()["restart_waiting"] == ["memory", "mods", "settings"]
        # A start uses them all.
        await ctx.server.restart(actor="test")
        assert await ctx.server.wait_online(20)
        assert downtime.waiting(ctx) == []
    finally:
        await finish(core)


async def test_difficulty_changes_at_once_and_waits_for_nothing(tmp_path):
    core, ctx, seen = await started(tmp_path)
    try:
        result = await gamesettings.save(ctx, updates={"difficulty": "hard"})
        assert result["running"] is True and result["restart_needed"] is False
        assert await wait_for(lambda: any("difficulty hard" in line for line in said(ctx)), 5)
        assert "settings" not in downtime.waiting(ctx)
        changed = next(e for e in seen if e.type == "game_settings_changed")
        assert "after a restart" not in changed.message
    finally:
        await finish(core)


async def test_nothing_waits_on_a_stopped_server(tmp_path):
    core = AgentCore(build_multi_config(tmp_path))
    ctx = core.servers["survival"]
    try:
        downtime.mark(ctx, "mods")  # stopped: the next start uses it anyway
        assert ctx.server.waiting == set() and downtime.waiting(ctx) == []
    finally:
        await core.jobs.stop()
        core.db.close()


# ------------------------------------------------ Restart when nobody's on
async def test_restart_when_nobody_is_on_waits_for_the_last_player(tmp_path, monkeypatch):
    monkeypatch.setattr(downtime, "EMPTY_CHECK_SECONDS", 0.1)
    core, ctx, seen = await started(tmp_path)
    try:
        await ctx.server.send_command("fakejoin Alex", internal=True)
        assert await wait_for(lambda: ctx.players.online_count() == 1, 5)
        pid = ctx.server.pid
        await downtime.restart_when_empty(ctx, "owner")
        assert ctx.status()["restart_when_empty"] is True
        await asyncio.sleep(0.5)
        assert ctx.server.pid == pid  # Alex is still playing: nobody is kicked
        await ctx.server.send_command("fakeleave Alex", internal=True)
        assert await wait_for(lambda: ctx.server.pid not in (None, pid), 15)
        assert await ctx.server.wait_online(20)
        assert ctx.server.restart_when_empty is False
        assert any(e.type == "restart_when_empty" for e in seen)
    finally:
        await finish(core)


async def test_restart_when_nobody_is_on_can_be_cancelled(tmp_path, monkeypatch):
    monkeypatch.setattr(downtime, "EMPTY_CHECK_SECONDS", 0.1)
    core, ctx, _seen = await started(tmp_path)
    try:
        await ctx.server.send_command("fakejoin Alex", internal=True)
        assert await wait_for(lambda: ctx.players.online_count() == 1, 5)
        pid = ctx.server.pid
        await downtime.restart_when_empty(ctx, "owner")
        await downtime.cancel_restart_when_empty(ctx, "owner")
        await ctx.server.send_command("fakeleave Alex", internal=True)
        await asyncio.sleep(0.5)
        assert ctx.server.pid == pid and ctx.server.restart_when_empty is False
    finally:
        await finish(core)


async def test_restart_when_nobody_is_on_needs_a_running_server(tmp_path):
    core = AgentCore(build_multi_config(tmp_path))
    ctx = core.servers["survival"]
    try:
        with pytest.raises(ServerError):
            await downtime.restart_when_empty(ctx, "owner")
    finally:
        await core.jobs.stop()
        core.db.close()


def test_the_routes_need_a_running_server(multi_client):
    answer = multi_client.post("/api/servers/survival/server/restart-when-empty")
    assert answer.status_code == 409
    answer = multi_client.post("/api/servers/survival/server/cancel-restart-when-empty")
    assert answer.status_code == 200
    status = multi_client.get("/api/servers/survival/status").json()
    assert status["restart_waiting"] == [] and status["restart_when_empty"] is False


# ------------------------------------------- the agent stopping and starting
async def test_the_agent_warns_stops_all_and_starts_them_again(tmp_path, monkeypatch):
    monkeypatch.setattr(downtime, "WARN_SECONDS", 0.2)
    cfg = build_multi_config(tmp_path)
    core = AgentCore(cfg)
    await core.start()
    a, b = core.servers["survival"], core.servers["creative"]
    await a.server.start()
    assert await a.server.wait_online(20)
    await a.server.send_command("fakejoin Alex", internal=True)
    assert await wait_for(lambda: a.players.online_count() == 1, 5)
    lines = []
    core.bus.subscribe(lambda e: lines.append(e.message) if e.type == "console" else None)
    await core.stop()
    assert not a.server.running and not b.server.running
    assert any("restarts in" in line for line in lines)  # Alex was told first

    again = AgentCore(cfg)
    await again.start()
    try:
        a2, b2 = again.servers["survival"], again.servers["creative"]
        assert await a2.server.wait_online(20)
        assert not b2.server.running  # it wasn't running before
        assert again.db.get_setting(downtime.RUNNING_KEY) == []
    finally:
        await stop_core(again)


async def test_nobody_on_means_no_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(downtime, "WARN_SECONDS", 30)
    core = AgentCore(build_multi_config(tmp_path))
    await core.start()
    ctx = core.servers["survival"]
    await ctx.server.start()
    assert await ctx.server.wait_online(20)
    await ctx.server.send_command("list", internal=True)
    assert await wait_for(lambda: ctx.players.online_count() == 0, 5)
    begun = time.monotonic()
    await core.stop()
    assert time.monotonic() - begun < 25
    assert not ctx.server.running


async def test_no_backup_after_stop_while_the_agent_stops(tmp_path):
    core = AgentCore(build_multi_config(tmp_path))
    ctx = core.servers["survival"]
    core.stopping = True
    from agent.events import Event

    event = Event(type="server_stopped", data={"reason": "user_stop"})
    ctx.config.set("backups.on_stop", True)
    assert ctx.backup_on_stop(event) is False
    await core.jobs.stop()
    core.db.close()


# ------------------------------------------------------- scheduled restarts
class Recorder:
    """The parts of a server a scheduled restart talks to."""

    server_id = "test"

    def __init__(self):
        self.said: list[str] = []
        self.restarted = False
        self.state = ServerState.ONLINE
        self.maintenance = False
        self.tps = None

    async def send_command(self, command, internal=False):
        self.said.append(command)
        return True

    async def restart(self, actor="system"):
        self.restarted = True


@pytest.fixture
def scheduler(config):
    db = Database(config.database_path)
    db.register_server("test", "Test", str(config.server_dir))
    server = Recorder()
    yield Scheduler(config, EventBus(), db, server), server
    db.close()


async def test_a_scheduled_restart_warns_again_10_seconds_before(scheduler, monkeypatch):
    sch, server = scheduler
    waits = []

    async def no_wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(sched.asyncio, "sleep", no_wait)
    await sch._task_restart({"warn_seconds": 60})
    assert server.said == [
        "say Server restarting in 60 seconds",
        "say Server restarting in 10 seconds",
    ]
    assert waits == [50, 10] and server.restarted


async def test_a_short_warning_is_said_once(scheduler, monkeypatch):
    sch, server = scheduler

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(sched.asyncio, "sleep", no_wait)
    await sch._task_restart({"warn_seconds": 5})
    assert server.said == ["say Server restarting in 5 seconds"]


async def test_a_restart_missed_while_asleep_is_skipped_not_run_late(scheduler):
    sch, server = scheduler
    row_id = sch.add("Nightly restart", "restart", "daily", "04:00", enabled=True)["id"]
    due = sch.get(row_id)["next_run"]
    sch.db.execute("UPDATE schedules SET next_run = ? WHERE id = ?", (due, row_id))
    # The PC woke up 3 hours after it was due.
    results = await sch.run_due(now=due + 3 * 3600)
    assert results[0]["result"].startswith("Skipped: it was missed")
    assert not server.restarted
    assert sch.get(row_id)["next_run"] > due + 3 * 3600  # waits for the next 04:00


async def test_a_backup_missed_while_asleep_still_runs(scheduler):
    sch, _server = scheduler
    row_id = sch.add("Nightly", "log_cleanup", "daily", "04:00", enabled=True)["id"]
    due = sch.get(row_id)["next_run"]
    results = await sch.run_due(now=due + 3 * 3600)
    assert not results[0]["result"].startswith("Skipped")


async def test_a_restart_on_time_runs(scheduler, monkeypatch):
    sch, server = scheduler

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(sched.asyncio, "sleep", no_wait)
    row_id = sch.add("Nightly restart", "restart", "daily", "04:00", enabled=True)["id"]
    due = sch.get(row_id)["next_run"]
    await sch.run_due(now=due + 30)
    assert server.restarted
