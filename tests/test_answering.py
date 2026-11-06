"""Online but not answering: the server list ping, the console check, the
plain-words reason and the backup-then-restart fix."""

from __future__ import annotations

import asyncio
import json
import socket
import threading

import pytest

from agent import recommendations
from agent.core import AgentCore
from agent.monitoring import answering
from agent.monitoring.answering import _varint, java_ping

from .conftest import build_multi_config


def serve_once(reply: bytes | None) -> tuple[int, threading.Thread]:
    """A one-connection TCP server: sends ``reply`` (None: says nothing)."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def run() -> None:
        conn, _ = listener.accept()
        with conn:
            conn.recv(1024)
            if reply is not None:
                conn.sendall(reply)
            else:
                conn.recv(1024)  # hold the line open until the client gives up
        listener.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return port, thread


def status_packet(status: dict) -> bytes:
    text = json.dumps(status).encode()
    body = b"\x00" + _varint(len(text)) + text
    return _varint(len(body)) + body


def test_ping_reads_the_servers_own_answer():
    port, thread = serve_once(
        status_packet({"version": {"name": "1.21.1"}, "players": {"online": 2, "max": 20}})
    )
    found = java_ping("127.0.0.1", port)
    thread.join(5)
    assert found == {
        "connected": True,
        "answer": {"version": "1.21.1", "players": 2, "max_players": 20},
    }


def test_ping_tells_a_closed_port_from_a_silent_one():
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    assert java_ping("127.0.0.1", port, timeout=1) == {"connected": False, "answer": None}

    port, thread = serve_once(None)
    assert java_ping("127.0.0.1", port, timeout=0.5) == {"connected": True, "answer": None}
    thread.join(5)


@pytest.fixture
async def core(tmp_path, monkeypatch):
    monkeypatch.setattr(answering, "CONSOLE_WAIT", 3.0)
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


def answers(game: bool, connected: bool = True):
    answer = {"version": "1.21.1", "players": 0, "max_players": 20} if game else None
    return lambda protocol, host, port: {"connected": connected, "answer": answer}


async def online(core):
    ctx = core.get_server("survival")
    await ctx.server.start()
    assert await ctx.server.wait_online(timeout=20)
    return ctx


def watch_events(ctx) -> list:
    seen: list = []
    ctx.core.bus.subscribe(lambda event: seen.append(event))
    return seen


async def test_a_server_that_answers_is_left_alone(core, monkeypatch):
    ctx = await online(core)
    monkeypatch.setattr(answering, "ping", answers(True))
    for _ in range(answering.CONSOLE_EVERY):
        status = await ctx.answering.check()
    assert status["ok"] is True and status["problem"] is None
    # The fifth check asked the real console, which answered "list".
    assert ctx.status()["answering"]["ok"] is True


async def test_three_misses_say_why_back_up_and_restart(core, monkeypatch):
    ctx = await online(core)
    core.config.set("monitor.auto_restart", True)
    monkeypatch.setattr(answering, "ping", answers(False, connected=False))
    seen = watch_events(ctx)
    ctx.server.console.append(
        "[12:00:00] [Server thread/WARN]: Can't keep up! Is the server overloaded?"
    )

    for _ in range(answering.MISSES - 1):
        assert (await ctx.answering.check())["problem"] is None  # one slow minute never counts
    status = await ctx.answering.check()

    problem = status["problem"]
    assert status["ok"] is False
    assert "Connection refused" in problem["what"]  # the console still answered
    assert problem["console"] == "answers" and problem["port_connects"] is False
    assert any("fell behind 1 time" in why for why in problem["why"])
    assert problem["fix"] == "The app took a backup and is restarting the server."
    alert = next(e for e in seen if e.type == "server_not_answering")
    assert problem["what"] in alert.message and problem["fix"] in alert.message
    backups = ctx.backups.list_backups()
    assert backups[0]["kind"] == "safety" and "stopped answering" in backups[0]["note"]
    for _ in range(200):  # the restart stops the server and starts it again
        if any(e.type == "server_restarted" for e in seen):
            break
        await asyncio.sleep(0.1)
    assert any(e.type == "server_restarted" for e in seen)
    assert await ctx.server.wait_online(timeout=20)


async def test_a_frozen_game_is_found_by_the_console(core, monkeypatch):
    ctx = await online(core)
    monkeypatch.setattr(answering, "ping", answers(True))

    async def silent() -> bool:
        return False

    monkeypatch.setattr(ctx.answering, "ask_console", silent)
    for _ in range(answering.CONSOLE_EVERY + answering.MISSES - 1):
        status = await ctx.answering.check()
    problem = status["problem"]
    assert problem["what"].startswith("The game is frozen")
    # Automatic restart is off in these tests: nothing was touched.
    assert problem["fix"] == "Automatic restart is off. Press Restart to fix it."
    assert ctx.backups.list_backups() == []
    rec = recommendations.evaluate(recommendations.gather(ctx))[0]
    assert rec.id == "not_answering" and rec.details["fix"]["kind"] == "restart"


async def test_answering_again_clears_it(core, monkeypatch):
    ctx = await online(core)
    seen = watch_events(ctx)
    monkeypatch.setattr(answering, "ping", answers(False))
    for _ in range(answering.MISSES):
        await ctx.answering.check()
    assert ctx.answering.problem["what"].startswith("Port")  # connects, never answers
    monkeypatch.setattr(answering, "ping", answers(True))
    status = await ctx.answering.check()
    assert status == {"ok": True, "checked_at": status["checked_at"], "problem": None}
    assert any(e.type == "server_answering_again" for e in seen)


async def test_no_backup_means_no_automatic_restart(core, monkeypatch):
    ctx = await online(core)
    core.config.set("monitor.auto_restart", True)
    monkeypatch.setattr(answering, "ping", answers(False))

    async def full(**_kwargs):
        raise RuntimeError("Not enough free disk space")

    monkeypatch.setattr(ctx.backups, "create", full)
    for _ in range(answering.MISSES):
        status = await ctx.answering.check()
    assert "couldn't take a backup first (Not enough free disk space)" in status["problem"]["fix"]
    await asyncio.sleep(0.5)
    assert ctx.server.state.value == "ONLINE"  # still running: the owner decides


async def test_the_watchdog_obeys_the_crash_loop_block(core, monkeypatch):
    ctx = await online(core)
    core.config.set("monitor.auto_restart", True)
    ctx.server.auto_restart_blocked = True
    ctx.server.auto_restart_block_reason = "5 crashes within 10 minutes"
    monkeypatch.setattr(answering, "ping", answers(False))
    for _ in range(answering.MISSES):
        status = await ctx.answering.check()
    assert "5 crashes within 10 minutes" in status["problem"]["fix"]
    assert ctx.backups.list_backups() == []  # nothing was done


async def test_a_server_stopped_during_the_backup_is_not_started_again(core, monkeypatch):
    ctx = await online(core)
    core.config.set("monitor.auto_restart", True)
    monkeypatch.setattr(answering, "ping", answers(False))

    async def someone_presses_stop(**_kwargs):
        await ctx.server.stop(actor="owner")

    monkeypatch.setattr(ctx.backups, "create", someone_presses_stop)
    for _ in range(answering.MISSES):
        status = await ctx.answering.check()
    assert "stopped" in status["problem"]["fix"]
    await asyncio.sleep(1.5)
    assert ctx.server.state.value == "OFFLINE"


async def test_a_server_that_answers_after_the_backup_is_left_running(core, monkeypatch):
    ctx = await online(core)
    core.config.set("monitor.auto_restart", True)
    monkeypatch.setattr(answering, "ping", answers(False))
    first_pid = ctx.server.pid

    async def backup_then_it_answers(**_kwargs):
        monkeypatch.setattr(answering, "ping", answers(True))

    monkeypatch.setattr(ctx.backups, "create", backup_then_it_answers)
    for _ in range(answering.MISSES):
        status = await ctx.answering.check()
    assert "answers again" in status["problem"]["fix"]
    await asyncio.sleep(1.5)
    assert ctx.server.pid == first_pid


async def test_a_failed_watchdog_restart_is_alerted(core, monkeypatch):
    ctx = await online(core)
    core.config.set("monitor.auto_restart", True)
    monkeypatch.setattr(answering, "ping", answers(False))
    seen = watch_events(ctx)

    async def backup(**_kwargs):
        return None

    async def no_luck(**_kwargs):
        return {"restarted": False, "error": "Server did not come back up"}

    monkeypatch.setattr(ctx.backups, "create", backup)
    monkeypatch.setattr(ctx.server, "restart", no_luck)
    for _ in range(answering.MISSES):
        await ctx.answering.check()
    for _ in range(50):
        if any(e.type == "server_restart_failed" for e in seen):
            break
        await asyncio.sleep(0.05)
    failed = [e for e in seen if e.type == "server_restart_failed"]
    assert failed and "did not come back up" in failed[0].message


async def test_a_manual_start_after_a_crash_wins_over_the_countdown(core):
    ctx = await online(core)  # someone pressed Start right after a crash
    core.config.set("monitor.auto_restart", True)
    await ctx.server._maybe_auto_restart()  # the crash's restart task runs late
    assert ctx.server.state.value == "ONLINE"
    assert ctx.server.restart_at is None


async def test_an_offline_server_is_not_judged(core, monkeypatch):
    ctx = core.get_server("survival")
    monkeypatch.setattr(answering, "ping", answers(False))
    for _ in range(answering.MISSES + 1):
        status = await ctx.answering.check()
    assert status == {"ok": None, "checked_at": None, "problem": None}


def test_the_apps_never_say_online_for_a_stuck_server(qapp):
    from desktop.server_tab import main_action, state_words

    # The desktop shows the agent's label and buttons, and decides neither.
    assert state_words({"state": "ONLINE", "label": "not_answering"}) == "Not answering"
    assert state_words({"state": "ONLINE", "label": "online"}) == "Online"
    assert state_words({"state": "ONLINE"}) == "Unknown"
    # A countdown to an automatic restart: the agent refuses Start.
    assert main_action({"state": "RESTART_PENDING", "label": "crashed", "can": []}) == (
        "start",
        False,
    )
    assert main_action({"label": "online", "can": ["stop", "restart"]}) == ("stop", True)
    assert main_action({"label": "online", "can": ["force_stop"]}) == ("force_stop", True)
    assert main_action({"label": "starting", "can": ["stop"]}) == ("stop", True)
    assert main_action({"label": "stopping", "can": []}) == ("stop", False)
