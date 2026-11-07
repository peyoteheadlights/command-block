"""Servers run as the separate Windows user (CommandBlockServer).

The agent side is driven with a fake "start as another account" (it records
the call, then starts the fake Minecraft server normally), a fake starter
service and a fake folder rights call, so no real Windows account is needed. The installer side uses
the installer tests' fake Windows. The real Windows calls are tried in
tests/windows/test_separate_user_live.py."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from agent import serveruser
from agent.core import AgentCore

from .conftest import build_multi_config


class Windows:
    """Records what the agent asked Windows to do."""

    def __init__(self, monkeypatch) -> None:
        self.started: list[dict] = []
        self.granted: list[tuple[Path, str]] = []
        self.removed: list[Path] = []
        self.starter: Exception | None = None  # what the fake service answers
        monkeypatch.setattr(serveruser, "launch_as", self.launch_as)
        monkeypatch.setattr(serveruser, "wait_for_starter", self.wait_for_starter)
        monkeypatch.setattr(serveruser, "grant", self.grant)
        monkeypatch.setattr(serveruser, "remove_grant", self.remove_grant)

    async def launch_as(self, cmd, cwd, popen):
        self.started.append({"cmd": cmd, "cwd": cwd})
        return await asyncio.create_subprocess_exec(*cmd, cwd=str(cwd), **popen)

    def wait_for_starter(self):
        if self.starter:
            raise self.starter

    def grant(self, folder, rights="M"):
        self.granted.append((Path(folder), rights))

    def remove_grant(self, folder):
        self.removed.append(Path(folder))


@pytest.fixture
def windows(monkeypatch):
    return Windows(monkeypatch)


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


def turn_on(config):
    config.set("security.separate_windows_user", True)


# ------------------------------------------------------------------ agent
async def test_off_starts_the_server_as_the_owner(core, windows):
    ctx = core.servers["survival"]
    await ctx.server.start()
    assert await ctx.server.wait_online(20)
    assert windows.started == [] and windows.granted == []


async def test_on_starts_the_server_as_the_separate_user_with_its_folder_opened(core, windows):
    turn_on(core.config)
    ctx = core.servers["survival"]
    await ctx.server.start()
    assert await ctx.server.wait_online(20)
    [started] = windows.started
    assert started["cwd"] == ctx.config.server_dir
    assert (ctx.config.server_dir, "M") in windows.granted
    # Console, stop: as before.
    await ctx.server.stop()
    assert not ctx.server.running


async def test_a_missing_starter_service_refuses_to_start_and_never_starts_as_the_owner(
    core, windows, monkeypatch
):
    turn_on(core.config)
    windows.starter = serveruser.ServerUserMissing(serveruser.NOT_RUNNING)
    real = asyncio.create_subprocess_exec
    owner_starts = []

    async def as_owner(*args, **kwargs):
        owner_starts.append(args)
        return await real(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", as_owner)
    ctx = core.servers["survival"]
    with pytest.raises(Exception, match="separate Windows user is missing"):
        await ctx.server.start()
    assert windows.started == [] and owner_starts == []
    assert not ctx.server.running


async def test_windows_refusing_the_account_reads_as_missing(core, monkeypatch, windows):
    turn_on(core.config)

    async def refused(cmd, cwd, popen):
        raise serveruser.ServerUserMissing("Windows error 1326")

    monkeypatch.setattr(serveruser, "launch_as", refused)
    ctx = core.servers["survival"]
    with pytest.raises(Exception, match="Open Settings and select Repair"):
        await ctx.server.start()
    assert not ctx.server.running


async def test_chunker_and_the_forge_installer_go_the_same_way(core, windows, tmp_path):
    from agent.servertypes import chunker, install

    turn_on(core.config)
    ctx = core.servers["survival"]
    script = tmp_path / "work" / "say.py"
    script.parent.mkdir()
    script.write_text("print('done')\n", encoding="utf-8")
    import sys

    process = await serveruser.start(
        ctx.config, [sys.executable, str(script)], cwd=script.parent,
        stdout=asyncio.subprocess.PIPE,
    )  # fmt: skip
    assert (await process.communicate())[0].strip() == b"done"
    assert windows.started[-1]["cwd"] == script.parent
    # Both call the same function (checked in the source, like the
    # command-isolation tests): there is no other way they start.
    import inspect

    assert "serveruser.start(" in inspect.getsource(chunker.run)
    assert "serveruser.start(" in inspect.getsource(install.run_installer)
    assert "create_subprocess_exec" not in inspect.getsource(chunker.run)
    assert "create_subprocess_exec" not in inspect.getsource(install.run_installer)


def test_settings_turn_on_needs_a_working_account(multi_client, multi, windows, monkeypatch):
    monkeypatch.setattr(serveruser, "IS_WINDOWS", True)
    monkeypatch.setattr(serveruser, "account_exists", lambda name=serveruser.ACCOUNT: True)
    monkeypatch.setattr(
        serveruser, "ask", fake_ask(serveruser.ServerUserMissing("Windows error 1326"))
    )
    answer = multi_client.post("/api/settings/separate-windows-user", json={"on": True})
    assert answer.status_code == 409
    assert "Repair" in answer.json()["detail"]
    assert multi.security.separate_windows_user is False


def test_settings_turn_on_and_off(multi_client, multi, windows, monkeypatch):
    monkeypatch.setattr(serveruser, "account_exists", lambda name=serveruser.ACCOUNT: True)
    monkeypatch.setattr(serveruser, "IS_WINDOWS", True)
    monkeypatch.setattr(serveruser, "ask", fake_ask(None))
    state = multi_client.get("/api/settings/separate-windows-user").json()
    assert state | {"restarting": []} == {
        "restarting": [],
        "on": False, "ok": True, "problem": None, "account": "CommandBlockServer",
        "available": True,
    }  # fmt: skip
    answer = multi_client.post("/api/settings/separate-windows-user", json={"on": True})
    assert answer.status_code == 200, answer.text
    assert answer.json()["on"] is True and answer.json()["restarting"] == []
    assert multi.security.separate_windows_user is True
    assert "separate_windows_user: true" in Path(multi.source).read_text()
    answer = multi_client.post("/api/settings/separate-windows-user", json={"on": False})
    assert answer.json()["on"] is False
    for server_id in multi.server_ids:
        assert multi.for_server(server_id).server_dir in windows.removed


def test_turn_off_restarts_running_servers_as_the_owner_before_taking_access_away(
    multi_client, multi, windows, monkeypatch
):
    import time

    turn_on(multi)
    order: list[str] = []
    windows_grant_removal = windows.remove_grant

    def remove(folder):
        order.append("removed")
        windows_grant_removal(folder)

    monkeypatch.setattr(serveruser, "remove_grant", remove)
    started = multi_client.post("/api/servers/survival/server/start")
    assert started.status_code == 200, started.text
    for _ in range(200):
        if multi_client.get("/api/servers/survival/status").json()["state"] == "ONLINE":
            break
        time.sleep(0.1)
    assert len(windows.started) == 1
    answer = multi_client.post("/api/settings/separate-windows-user", json={"on": False})
    assert answer.status_code == 200, answer.text
    assert answer.json()["restarting"] == ["survival"]
    # Back up as the owner (no second start as the separate user), and only
    # then was the folder access removed.
    assert len(windows.started) == 1
    assert multi_client.get("/api/servers/survival/status").json()["state"] in (
        "STARTING",
        "ONLINE",
    )
    assert multi.for_server("survival").server_dir in windows.removed
    assert order


def test_only_a_real_java_folder_is_opened(tmp_path):
    home = tmp_path / "jdk-21"
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "java").write_text("")
    assert serveruser.java_home(str(home / "bin" / "java")) is None  # no "release" file
    (home / "release").write_text('JAVA_VERSION="21"\n')
    assert serveruser.java_home(str(home / "bin" / "java")) == home.resolve()
    assert serveruser.java_home("no-such-java") is None


def fake_ask(problem):
    def ask(request):
        assert request == {"do": "ping"}
        if problem:
            raise problem
        return {"ok": True}

    return ask


def test_settings_status_says_what_is_broken(multi_client, multi, monkeypatch):
    def problem():
        return multi_client.get("/api/settings/separate-windows-user").json()["problem"]

    monkeypatch.setattr(serveruser, "IS_WINDOWS", True)
    monkeypatch.setattr(serveruser, "account_exists", lambda name=serveruser.ACCOUNT: False)
    assert problem() == "account"
    monkeypatch.setattr(serveruser, "account_exists", lambda name=serveruser.ACCOUNT: True)
    not_running = serveruser.ServerUserMissing(serveruser.NOT_RUNNING)
    monkeypatch.setattr(serveruser, "ask", fake_ask(not_running))
    assert problem() == "service"
    wrong = serveruser.ServerUserMissing("Windows error 1326")
    monkeypatch.setattr(serveruser, "ask", fake_ask(wrong))
    assert problem() == "password"


# ------------------------------------------------------------------ starter service
def test_the_starter_settings_are_written_and_read_back(tmp_path):
    from agent import starter

    path = tmp_path / "starter.json"
    starter.write_settings(path, "Abc-123", "S-1-5-21-1-2-3-1001")
    assert starter.read_settings(path) == ("Abc-123", "S-1-5-21-1-2-3-1001")
    # The account id goes into an access list, so only a real SID is taken.
    with pytest.raises(ValueError):
        starter.write_settings(path, "Abc-123", "S-1-5-21)(A;;GA;;;WD")


def test_the_starter_checks_every_request():
    from agent.starter import check_request

    good = {
        "do": "start",
        "command_line": '"C:\\Java\\bin\\java.exe" -jar server.jar',
        "cwd": "/srv/survival" if os.name != "nt" else "C:\\srv",
        "application": None,
        "flags": 0x08000000 | 0x100 | 0x200,  # NO_WINDOW and REALTIME are dropped
        "handles": [4, 8, 8],
    }
    checked = check_request(good)
    assert checked["flags"] == 0x200
    assert check_request({"do": "ping", "extra": 1}) == {"do": "ping"}
    for bad in (
        [],
        {"do": "run-as-system"},
        {**good, "cwd": "relative"},
        {**good, "command_line": ""},
        {**good, "command_line": "java\0x"},
        {**good, "application": "java.exe"},
        {**good, "flags": True},
        {**good, "handles": [1, 2]},
        {**good, "handles": [1, 2, -3]},
        {**good, "handles": [1, 2, "3"]},
    ):
        with pytest.raises(ValueError):
            check_request(bad)


# ------------------------------------------------------------------ desktop app
def desktop_settings(qapp, agent_http, monkeypatch, technical=True, works=True):
    """The PC settings page against the real agent. The account check is
    faked: ``account[0]`` says whether the account works."""
    from desktop.agent import AgentClient
    from desktop.window import MainWindow

    from .fake_pc import FakePC
    from .test_desktop_app import sign_in, wait_for

    account = [works]

    def status(config):
        return {
            "on": serveruser.is_on(config),
            "ok": account[0],
            "problem": None if account[0] else "password",
            "account": serveruser.ACCOUNT,
            "available": True,
        }

    monkeypatch.setattr(serveruser, "status", status)
    pc = FakePC()
    window = MainWindow(AgentClient(agent_http, place="this PC"), pc, dark=False)
    window.show()
    sign_in(qapp, window)
    wait_for(qapp, lambda: window.role == "owner")
    window.set_mode(technical)
    window.open_list("PC settings", "pc")
    return window, pc, window.gear.sections["pc"], account


def test_desktop_turn_on_runs_the_installer_then_tells_the_agent(
    qapp, agent_http, monkeypatch, windows
):
    from .test_desktop_app import button, wait_for
    from .test_desktop_gear import shows

    config = agent_http.agent.config
    window, pc, page, _ = desktop_settings(qapp, agent_http, monkeypatch)
    shows(qapp, page, "Turn on")
    button(page, "Turn on").click()
    wait_for(qapp, lambda: config.security.separate_windows_user is True)
    assert pc.server_user_runs == ["on"]
    shows(qapp, page, "Turn off")  # Technical
    button(page, "Turn off").click()
    wait_for(qapp, lambda: pc.server_user_runs == ["on", "off"])
    assert config.security.separate_windows_user is False
    window.close()


def test_desktop_shows_repair_when_the_account_is_broken_and_simple_has_no_turn_off(
    qapp, agent_http, monkeypatch, windows
):
    from .test_desktop_app import button, wait_for
    from .test_desktop_gear import shown_buttons, shows

    config = agent_http.agent.config
    config.set("security.separate_windows_user", True)
    window, pc, page, account = desktop_settings(
        qapp, agent_http, monkeypatch, technical=False, works=False
    )
    shows(qapp, page, "Repair")
    pc.server_user_code = 1223  # Windows was told No
    button(page, "Repair").click()
    wait_for(qapp, lambda: "didn't give permission" in page.note.text())
    assert pc.server_user_runs == ["repair"]
    # Working again: Simple mode offers no Turn off.
    account[0] = True
    page.load()
    wait_for(qapp, lambda: "Repair" not in shown_buttons(page))
    assert "Turn off" not in shown_buttons(page)
    window.close()
