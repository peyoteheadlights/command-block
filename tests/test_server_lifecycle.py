"""The server's life around the agent's own: what happens to a running
Minecraft server when the agent stops, crashes or starts again."""

import asyncio
import json

import psutil
import pytest

from agent import downtime
from agent.core import AgentCore
from agent.events import EventBus
from agent.minecraft.process import PID_FILE, MinecraftServer, ServerError
from agent.minecraft.state import ExitReason, ServerState

from .conftest import build_multi_config


def collector(bus):
    seen = []
    bus.subscribe(lambda e: seen.append(e))
    return seen


async def started(config) -> MinecraftServer:
    server = MinecraftServer(config, EventBus())
    await server.start()
    assert await server.wait_online(timeout=15)
    return server


# ---------------------------------------------------------------- left behind
async def test_a_server_left_by_a_crashed_agent_is_found_and_never_started_twice(config):
    old = await started(config)
    pid = old.pid
    assert json.loads((config.server_dir / PID_FILE).read_text())["pid"] == pid
    await old.shutdown()  # the agent died; Minecraft did not

    bus = EventBus()
    seen = collector(bus)
    server = MinecraftServer(config, bus)
    try:
        assert server.verify_state() is ServerState.ONLINE
        assert server.left_behind and server.pid == pid and server.running
        assert "before the agent" in server.status()["state_source"]
        with pytest.raises(ServerError, match="already"):
            await server.start()
        # No console: a clean stop has to come from the game itself.
        with pytest.raises(ServerError, match="/stop"):
            await server.stop()
        with pytest.raises(ServerError, match="console"):
            await server.send_command("say hi")
        await server.watch_left_behind()
        await server.kill(actor="tester")
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == "zombie"
        assert server.state is ServerState.OFFLINE
        assert server.last_exit_reason is ExitReason.FORCE_KILLED
        assert not (config.server_dir / PID_FILE).exists()
        assert [e for e in seen if e.type == "server_stopped"]
    finally:
        if old.process and old.process.returncode is None:
            old.process.kill()


async def test_a_left_behind_server_stopped_in_game_is_a_clean_stop(config):
    old = await started(config)
    await old.shutdown()
    bus = EventBus()
    seen = collector(bus)
    server = MinecraftServer(config, bus)
    server.verify_state()
    await server.watch_left_behind()
    # Someone typed /stop in the game: the only clean way without a console.
    old.process.stdin.write(b"stop\n")
    await old.process.stdin.drain()
    assert await server.wait_exit(timeout=15)
    assert server.state is ServerState.OFFLINE
    # Here the test process reaped it, so its exit code was gone: nothing
    # is claimed. On Windows the code is read, and 0 is a clean stop.
    assert server.last_exit_reason in (ExitReason.CLEAN_EXIT, ExitReason.UNKNOWN)
    assert not server.left_behind
    assert not [e for e in seen if e.type == "server_crashed"]


def test_a_left_behind_exit_is_judged_by_its_exit_code_only(config):
    server = MinecraftServer(config, EventBus())
    server.left_behind = psutil.Process()
    assert server._classify_exit(0) is ExitReason.CLEAN_EXIT
    assert server._classify_exit(1) is ExitReason.CRASH
    assert server._classify_exit(None) is ExitReason.UNKNOWN


async def test_a_stale_pid_file_is_not_taken_for_our_server(config):
    # The number belongs to a live process that is not this server (this
    # test itself), as can happen after a reboot.
    me = psutil.Process()
    (config.server_dir / PID_FILE).write_text(
        json.dumps({"pid": me.pid, "created": me.create_time() - 1000}), encoding="utf-8"
    )
    server = MinecraftServer(config, EventBus())
    assert server.verify_state() is ServerState.OFFLINE
    assert not server.left_behind
    assert not (config.server_dir / PID_FILE).exists()


async def test_a_normal_stop_removes_the_pid_file(config):
    server = await started(config)
    await server.stop()
    assert not (config.server_dir / PID_FILE).exists()


# ---------------------------------------------------- stop with the agent
async def test_the_agent_stops_its_servers_and_starts_them_again(tmp_path):
    config = build_multi_config(tmp_path)
    core = AgentCore(config)
    try:
        for ctx in core.servers.values():
            await ctx.start()
        survival = core.get_server("survival")
        await survival.server.start()
        assert await survival.server.wait_online(timeout=20)
        seen = collector(core.bus)
        await downtime.stop_for_agent(core)
        assert survival.server.state is ServerState.OFFLINE
        assert survival.server.last_exit_reason is ExitReason.USER_STOP
        # Nobody was on, so no warning and no wait (test_less_downtime has the warning).
        assert not any("say" in line.raw for line in survival.server.console.tail(20))
        assert [e for e in seen if e.type == "server_stopped"]
        for ctx in core.servers.values():
            await ctx.stop()
    finally:
        await core.jobs.stop()
        core.db.close()

    again = AgentCore(build_multi_config(tmp_path))
    try:
        for ctx in again.servers.values():
            await ctx.start()
        await downtime.start_after_agent(again)
        ctx = again.get_server("survival")
        assert await ctx.server.wait_online(timeout=20)
        others = [c for c in again.servers.values() if c.server_id != "survival"]
        assert all(not c.server.running for c in others)
        # Only once: the next agent start doesn't start it again by itself.
        assert again.db.get_setting(downtime.RUNNING_KEY) in (None, [])
        await ctx.server.stop()
        for c in again.servers.values():
            await c.stop()
    finally:
        await again.jobs.stop()
        again.db.close()


async def test_the_agent_stops_its_servers_at_the_same_time(tmp_path, monkeypatch):
    core = AgentCore(build_multi_config(tmp_path))
    stopping: list[str] = []
    finished = asyncio.Event()

    async def slow_stop(self, **kwargs):
        stopping.append(self.server_id)
        await finished.wait()
        return {}

    try:
        for ctx in core.servers.values():
            ctx.server.state = ServerState.ONLINE
            monkeypatch.setattr(type(ctx.server), "running", property(lambda s: True))
        monkeypatch.setattr(MinecraftServer, "stop", slow_stop)
        monkeypatch.setattr(MinecraftServer, "send_command", lambda *a, **k: _done())
        monkeypatch.setattr(downtime, "WARN_SECONDS", 0.0)
        task = asyncio.create_task(downtime.stop_for_agent(core))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if len(stopping) == len(core.servers):
                break
        assert len(stopping) == len(core.servers)  # all at once, not one by one
        finished.set()
        await task
    finally:
        await core.jobs.stop()
        core.db.close()


async def _done():
    return True


# ------------------------------------------------- kept over an agent restart
async def test_sleep_and_the_crash_loop_block_are_kept_over_an_agent_restart(tmp_path):
    config = build_multi_config(tmp_path)
    core = AgentCore(config)
    try:
        server = core.get_server("survival").server
        server.stopped_for_sleep = True
        server.record_crash()
        server.auto_restart_blocked = True
        server.auto_restart_block_reason = "5 crashes within 10 minutes"
        server.remember()
    finally:
        await core.jobs.stop()
        core.db.close()

    again = AgentCore(build_multi_config(tmp_path))
    try:
        server = again.get_server("survival").server
        assert server.stopped_for_sleep is True
        assert server.auto_restart_blocked is True
        assert server.auto_restart_block_reason == "5 crashes within 10 minutes"
        assert server.recent_crash_count() == 1
        other = again.get_server("creative").server
        assert other.stopped_for_sleep is False and other.auto_restart_blocked is False
    finally:
        await again.jobs.stop()
        again.db.close()


async def test_the_watchdogs_fix_count_is_kept_over_an_agent_restart(tmp_path):
    core = AgentCore(build_multi_config(tmp_path))
    try:
        watch = core.get_server("survival").answering
        watch.note_fix()
        watch.note_fix()
    finally:
        await core.jobs.stop()
        core.db.close()
    again = AgentCore(build_multi_config(tmp_path))
    try:
        assert again.get_server("survival").answering.recent_fixes() == 2
        assert again.get_server("creative").answering.recent_fixes() == 0
    finally:
        await again.jobs.stop()
        again.db.close()
