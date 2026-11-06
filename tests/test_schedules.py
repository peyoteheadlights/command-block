"""Schedules run on time or not at all, and maintenance mode has one owner."""

import time

import pytest
import yaml

from agent.core import AgentCore
from agent.scheduler import scheduler as scheduler_module

from .conftest import build_multi_config


@pytest.fixture
async def core(tmp_path):
    agent = AgentCore(build_multi_config(tmp_path))
    for ctx in agent.servers.values():
        await ctx.start()
    try:
        yield agent
    finally:
        for ctx in list(agent.servers.values()):
            if ctx.server.running:
                await ctx.server.stop()
            await ctx.stop()
        await agent.jobs.stop()
        agent.db.close()


def saved(core) -> bool:
    data = yaml.safe_load(core.config.source.read_text(encoding="utf-8"))
    return data["maintenance"]["enabled"] is True


def schedule(ctx, task, payload=None, due=0.0):
    row = ctx.scheduler.add(f"{task} test", task, "daily", "04:00", payload=payload)
    ctx.db.execute("UPDATE schedules SET next_run = ? WHERE id = ?", (time.time() - due, row["id"]))
    return row["id"]


async def online(core, server_id="survival"):
    ctx = core.get_server(server_id)
    await ctx.server.start()
    assert await ctx.server.wait_online(timeout=20)
    return ctx


async def test_a_run_missed_while_the_pc_slept_is_skipped_not_run_late(core):
    ctx = core.get_server("survival")
    row_id = schedule(ctx, "start", due=3 * 3600)  # 02:00's start, at 05:00
    await ctx.scheduler.run_due()
    row = ctx.scheduler.get(row_id)
    assert row["last_result"].startswith("Skipped") and "late" in row["last_result"]
    assert row["next_run"] > time.time()
    assert not ctx.server.running


async def test_a_backup_still_runs_late(core):
    ctx = core.get_server("survival")
    row_id = schedule(ctx, "backup", due=3 * 3600)
    await ctx.scheduler.run_due()
    assert ctx.scheduler.get(row_id)["last_result"].startswith("Created")


async def test_the_next_run_is_saved_before_the_task_runs(core, monkeypatch):
    ctx = core.get_server("survival")
    row_id = schedule(ctx, "backup", due=1)
    seen = {}

    async def backup(payload):
        seen["next_run"] = ctx.scheduler.get(row_id)["next_run"]
        return "done"

    monkeypatch.setitem(ctx.scheduler.handlers, "backup", backup)
    await ctx.scheduler.run_due()
    assert seen["next_run"] > time.time()  # a cut-off run is not run again at boot


async def test_a_server_stopped_during_the_warning_is_not_started_again(core, monkeypatch):
    ctx = await online(core)

    async def someone_stops_it(seconds):
        if ctx.server.running:
            await ctx.server.stop(actor="owner")

    monkeypatch.setattr(scheduler_module, "_wait", someone_stops_it)
    row_id = schedule(ctx, "restart", payload={"warn_seconds": 60}, due=1)
    await ctx.scheduler.run_due()
    assert ctx.scheduler.get(row_id)["last_result"].startswith("Skipped")
    assert not ctx.server.running


async def test_players_are_warned_again_ten_seconds_before(core, monkeypatch):
    ctx = await online(core)
    waits = []

    async def wait(seconds):
        waits.append(seconds)

    said = []
    send = ctx.server.send_command

    async def record(command, internal=False):
        said.append(command)
        return await send(command, internal=internal)

    monkeypatch.setattr(scheduler_module, "_wait", wait)
    monkeypatch.setattr(ctx.server, "send_command", record)
    schedule(ctx, "stop", payload={"warn_seconds": 60}, due=1)
    await ctx.scheduler.run_due()
    assert any("60 seconds" in s for s in said) and any("10 seconds" in s for s in said)
    assert waits == [50, 10]
    assert not ctx.server.running


async def test_a_notification_cant_pretend_to_be_a_server_event(core):
    ctx = core.get_server("survival")
    seen = []
    core.bus.subscribe(lambda e: seen.append(e))
    schedule(ctx, "notify", payload={"as": "started", "message": "hi"}, due=1)
    await ctx.scheduler.run_due()
    assert not [e for e in seen if e.type == "server_started"]


# ------------------------------------------------------------ maintenance
async def test_the_maintenance_button_is_saved_and_reaches_every_server(core):
    core.set_maintenance(True, user="owner")
    assert all(ctx.server.maintenance for ctx in core.servers.values())
    assert saved(core)


async def test_scheduled_maintenance_goes_through_the_same_switch(core):
    ctx = core.get_server("survival")
    schedule(ctx, "maintenance_on", due=1)
    await ctx.scheduler.run_due()
    assert all(c.server.maintenance for c in core.servers.values())
    assert core.config.maintenance.enabled is True
    assert saved(core)


def test_maintenance_from_settings_reaches_the_running_servers(client):
    from .conftest import PASSWORD

    token = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    headers = {"Authorization": f"Bearer {token.json()['token']}"}
    response = client.put(
        "/api/settings", json={"updates": {"maintenance.enabled": True}}, headers=headers
    )
    assert response.status_code == 200, response.text
    core = client.app.state.core
    assert all(ctx.server.maintenance for ctx in core.servers.values())
