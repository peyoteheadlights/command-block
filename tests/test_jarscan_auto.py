"""The automatic side of the jar check and the deep scan: the stop after a
minute while the server runs, Ask me first, the checks before start with
Skip, the daily or weekly deep scan and who is told about Known bad."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from agent.events import Event
from agent.minecraft.process import ServerError
from agent.mods import deepscan, jarscan
from agent.security import permissions as P

from .test_deepscan import BAD_CLASSES, CLASS, amsi  # noqa: F401
from .test_helpers import add_helper
from .test_jarscan import BASE, Sites, bad_jar, mod_with, row
from .test_moderators import give, owner  # noqa: F401
from .test_phone_app import send


@pytest.fixture
def ctx(client, monkeypatch):
    from .test_api import auth, token_for

    client.headers.update(auth(token_for(client)))
    monkeypatch.setattr(deepscan, "java_root", lambda java: None)
    return client.app.state.core.servers["test"]


@pytest.fixture
def sites(ctx, monkeypatch):
    return Sites(ctx, monkeypatch)


class Running:
    """Stands in for a running server: chat commands and the stop are kept."""

    def __init__(self, ctx, monkeypatch):
        self.on = True
        self.said: list[str] = []
        self.stopped: list[str] = []
        monkeypatch.setattr(type(ctx.server), "running", property(lambda _: self.on))

        async def send_command(command, internal=False):
            self.said.append(command)
            return True

        async def stop(actor, reason=None):
            self.stopped.append(actor)
            self.on = False

        monkeypatch.setattr(ctx.server, "send_command", send_command)
        monkeypatch.setattr(ctx.server, "stop", stop)
        monkeypatch.setattr(jarscan, "STOP_AFTER", 0)


@pytest.fixture
def running(ctx, monkeypatch):
    return Running(ctx, monkeypatch)


def wait_for_stop(client, ctx):
    async def wait():
        if ctx.jarscan._stop_task:
            await ctx.jarscan._stop_task

    client.portal.call(wait)


def events_of(ctx, monkeypatch, kind):
    seen: list[Event] = []
    publish = ctx.bus.publish

    async def keep(event):
        if event.type == kind:
            seen.append(event)
        await publish(event)

    monkeypatch.setattr(ctx.bus, "publish", keep)
    return seen


# ------------------------------------------------------------ while running
def test_known_bad_while_running_warns_in_chat_then_stops_and_moves_it(
    client, ctx, sites, running, monkeypatch
):
    monkeypatch.setattr(jarscan, "STOP_AFTER", 0.5)
    found = events_of(ctx, monkeypatch, "jar_scan_found")
    path = bad_jar(ctx)
    report = client.post(f"{BASE}/mods/scan").json()
    assert row(report, "bad.jar")["words"] == "jarscan.row.waiting"
    assert running.said == [jarscan.STOP_WARNING]
    assert "stops in 1 minute" in found[0].message

    wait_for_stop(client, ctx)
    assert running.stopped == ["jar check"]
    assert not path.exists()
    assert ctx.jarscan.report()["quarantine"][0]["filename"] == "bad.jar"


def test_a_second_finding_doesnt_plan_a_second_stop(client, ctx, sites, running, monkeypatch):
    monkeypatch.setattr(jarscan, "STOP_AFTER", 3600)
    bad_jar(ctx, "one.jar")
    client.post(f"{BASE}/mods/scan")
    bad_jar(ctx, "two.jar")
    client.post(f"{BASE}/mods/scan")
    assert running.said == [jarscan.STOP_WARNING]
    client.portal.call(asyncio.sleep, 0)
    ctx.jarscan._stop_task.cancel()


def test_ask_me_first_keeps_the_server_running(client, ctx, sites, running, monkeypatch):
    found = events_of(ctx, monkeypatch, "jar_scan_found")
    assert client.post(f"{BASE}/mods/scan/options", json={"ask_first": True}).status_code == 200
    path = bad_jar(ctx)
    client.post(f"{BASE}/mods/scan")
    wait_for_stop(client, ctx)
    assert running.said == [] and running.stopped == []
    assert path.is_file() and running.on
    assert "Ask me first is on" in found[0].message


def test_the_stop_is_called_off_when_the_file_was_moved_already(
    client, ctx, sites, running, monkeypatch
):
    monkeypatch.setattr(jarscan, "STOP_AFTER", 0.5)
    bad_jar(ctx)
    client.post(f"{BASE}/mods/scan")
    saved = ctx.jarscan.store()
    for entry in saved["jars"].values():
        entry["waiting"] = False  # the owner stopped it and it moved
    ctx.jarscan.save(saved)
    wait_for_stop(client, ctx)
    assert running.stopped == []


def test_turning_on_ask_me_first_during_the_minute_calls_the_stop_off(
    client, ctx, sites, running, monkeypatch
):
    monkeypatch.setattr(jarscan, "STOP_AFTER", 0.5)
    bad_jar(ctx)
    client.post(f"{BASE}/mods/scan")
    assert client.post(f"{BASE}/mods/scan/options", json={"ask_first": True}).status_code == 200
    wait_for_stop(client, ctx)
    assert running.stopped == []


def test_a_new_run_gets_its_own_warning_and_minute(client, ctx, sites, running, monkeypatch):
    monkeypatch.setattr(jarscan, "STOP_AFTER", 0.5)
    ctx.server.started_at = 1.0
    bad_jar(ctx, "one.jar")
    client.post(f"{BASE}/mods/scan")
    first = ctx.jarscan._stop_task
    ctx.server.started_at = 2.0  # stopped and started again
    bad_jar(ctx, "two.jar")
    client.post(f"{BASE}/mods/scan")
    assert running.said == [jarscan.STOP_WARNING, jarscan.STOP_WARNING]
    client.portal.call(asyncio.sleep, 0)
    assert first.cancelled()
    wait_for_stop(client, ctx)
    assert running.stopped == ["jar check"]


def test_worth_a_look_never_stops_the_server(client, ctx, running):
    mod_with(
        ctx.config.mods_dir / "odd.jar", {"a/B.class": CLASS + BAD_CLASSES["starts_a_program"]}
    )
    client.portal.call(ctx.deepscan.run_now, "mods", "test")
    assert ctx.deepscan.summary()["worth_a_look"] == 1
    assert running.said == [] and ctx.jarscan._stop_task is None


def test_known_bad_from_a_deep_scan_while_running_stops_the_server(
    client,
    ctx,
    running,
    amsi,  # noqa: F811
):
    from .test_antivirus import MARK

    bad = Path(ctx.config.server_dir) / "evil.dat"
    bad.write_bytes(MARK)
    client.portal.call(ctx.deepscan.run_now, "folder", "test")
    assert running.said == [jarscan.STOP_WARNING]
    wait_for_stop(client, ctx)
    assert running.stopped == ["jar check"]
    assert not bad.exists()


def test_only_the_owner_can_change_ask_me_first(multi_client, owner):  # noqa: F811
    sam = add_helper(multi_client, owner)
    give(multi_client, owner, {"survival": P.RUN_IT | {P.DEEP_SCAN}})
    url = "/api/servers/survival/mods/scan/options"
    assert multi_client.post(url, json={"ask_first": True}, headers=sam).status_code == 403
    assert multi_client.post(url, json={"before_start": True}, headers=sam).status_code == 200
    assert multi_client.post(url, json={"ask_first": True}, headers=owner).status_code == 200


# ------------------------------------------------------------ before start
def test_the_checks_before_start_are_off_by_default(ctx):
    assert not ctx.jarscan.checks_before_start()
    assert ctx.deepscan.options()["before_start"] is False
    assert ctx.deepscan.options()["schedule"] == "off"
    assert ctx.deepscan.options()["time"] == "04:00"


def test_the_jar_check_before_start_finds_a_new_jar_and_keeps_it_out(client, ctx, sites):
    client.post(f"{BASE}/mods/scan/options", json={"before_start": True})
    path = bad_jar(ctx)
    sites.down = False
    with pytest.raises(ServerError, match="bad.jar to quarantine"):
        client.portal.call(ctx.jarscan.before_start)
    assert sites.asked >= 1  # a full check, with the lookup, not only the quick one
    assert not path.exists()
    assert ctx.server.checking_jars is False
    client.portal.call(ctx.jarscan.before_start)  # the next start goes on without it


def test_the_deep_scan_before_start_runs_without_downloading(client, ctx, monkeypatch):
    client.post(f"{BASE}/mods/deepscan/options", json={"before_start": True})
    mod_with(ctx.config.mods_dir / "x.jar", {"a/B.class": CLASS})

    async def no_download():
        raise AssertionError("a deep scan before start downloads nothing")

    monkeypatch.setattr(ctx.deepscan, "get_forge", no_download)
    client.portal.call(ctx.jarscan.before_start)
    assert ctx.deepscan.store()["last"]["by"] == "agent"


def test_skip_lets_the_start_go_on(client, ctx, monkeypatch):
    client.post(f"{BASE}/mods/scan/options", json={"before_start": True})
    forever = asyncio.Event()

    async def slow_checks():
        await forever.wait()

    monkeypatch.setattr(ctx.jarscan, "_checks", slow_checks)
    assert client.post(f"{BASE}/server/skip-check").status_code == 409
    starting = client.portal.start_task_soon(ctx.jarscan.before_start)
    deadline = time.time() + 5
    while not ctx.server.checking_jars and time.time() < deadline:
        time.sleep(0.02)
    assert ctx.jarscan.report()["checking"] is True
    assert client.get(f"{BASE}/status").json()["checking_jars"] is True
    assert client.post(f"{BASE}/server/skip-check").status_code == 200
    starting.result(timeout=5)
    assert ctx.server.checking_jars is False
    assert ctx.jarscan.report()["checking"] is False


def test_a_check_that_fails_doesnt_hold_the_start(client, ctx, monkeypatch):
    client.post(f"{BASE}/mods/scan/options", json={"before_start": True})

    async def broken():
        raise RuntimeError("disk gone")

    monkeypatch.setattr(ctx.jarscan, "_checks", broken)
    client.portal.call(ctx.jarscan.before_start)
    assert ctx.server.checking_jars is False


def test_only_the_owner_can_skip(multi_client, owner):  # noqa: F811
    sam = add_helper(multi_client, owner)
    give(multi_client, owner, {"survival": P.RUN_IT})
    url = "/api/servers/survival/server/skip-check"
    assert multi_client.post(url, headers=sam).status_code == 403


def test_start_answers_at_once_while_the_jars_are_checked(client, ctx, monkeypatch):
    client.post(f"{BASE}/mods/scan/options", json={"before_start": True})
    started = asyncio.Event()

    async def fake_start(actor=None):
        started.set()
        return {"pid": 1}

    monkeypatch.setattr(ctx.server, "start", fake_start)
    answer = client.post(f"{BASE}/server/start")
    assert answer.status_code == 200, answer.text
    assert answer.json()["result"] == "CHECKING"
    client.portal.call(asyncio.sleep, 0.05)
    assert started.is_set()


def test_a_second_start_while_checking_is_refused(client, ctx, monkeypatch):
    client.post(f"{BASE}/mods/scan/options", json={"before_start": True})
    monkeypatch.setattr(ctx.server, "checking_jars", True)
    assert client.post(f"{BASE}/server/start").status_code == 409


def test_a_start_that_cant_happen_says_so_at_once(client, ctx, monkeypatch):
    from agent.minecraft.state import ServerState

    client.post(f"{BASE}/mods/scan/options", json={"before_start": True})
    monkeypatch.setattr(ctx.server, "state", ServerState.RESTART_PENDING)
    answer = client.post(f"{BASE}/server/start")
    assert answer.status_code == 409
    assert "Restart Now" in answer.json()["detail"]
    assert ctx.server.checking_jars is False


def test_a_start_that_fails_after_the_checks_says_so(client, ctx, monkeypatch):
    client.post(f"{BASE}/mods/scan/options", json={"before_start": True})
    failed = events_of(ctx, monkeypatch, "start_failed")

    async def broken_start(actor=None):
        assert ctx.server.checking_jars  # set before the task began
        raise RuntimeError("java went missing")

    monkeypatch.setattr(ctx.server, "start", broken_start)
    assert client.post(f"{BASE}/server/start").json()["result"] == "CHECKING"
    client.portal.call(asyncio.sleep, 0.05)
    assert "java went missing" in failed[0].message
    assert ctx.server.checking_jars is False


# ------------------------------------------------------------ schedule
def test_the_schedule_works_out_its_next_run(client, ctx):
    body = {"schedule": "weekly", "day": "mon", "time": "03:30"}
    answer = client.post(f"{BASE}/mods/deepscan/options", json=body)
    assert answer.status_code == 200, answer.text
    options = answer.json()["options"]
    assert options["schedule"] == "weekly" and options["day"] == "mon"
    assert options["next"] > time.time()
    assert time.localtime(options["next"]).tm_wday == 0
    off = client.post(f"{BASE}/mods/deepscan/options", json={"schedule": "off"}).json()
    assert off["options"]["next"] is None


def test_a_bad_time_is_refused(client, ctx):
    answer = client.post(f"{BASE}/mods/deepscan/options", json={"time": "25:00"})
    assert answer.status_code == 422


def test_the_scheduled_scan_runs_when_due_with_installed_scanners_only(client, ctx, monkeypatch):
    client.post(f"{BASE}/mods/deepscan/options", json={"schedule": "daily"})
    client.portal.call(ctx.deepscan.tick)
    assert ctx.deepscan.store().get("last") is None  # not due yet

    async def no_download():
        raise AssertionError("the schedule downloads nothing")

    monkeypatch.setattr(ctx.deepscan, "get_forge", no_download)
    saved = ctx.deepscan.store()
    saved["next"] = time.time() - 1
    ctx.deepscan.save(saved)
    client.portal.call(ctx.deepscan.tick)
    last = ctx.deepscan.store()["last"]
    assert last["by"] == "schedule"
    assert last["scanners"] == [deepscan.BEHAVIOUR]
    assert ctx.deepscan.store()["next"] > time.time()


def test_a_due_run_waits_while_another_scan_runs(client, ctx, monkeypatch):
    client.post(f"{BASE}/mods/deepscan/options", json={"schedule": "daily"})
    saved = ctx.deepscan.store()
    saved["next"] = due = time.time() - 1
    ctx.deepscan.save(saved)
    monkeypatch.setattr(ctx.deepscan, "_running", lambda: True)
    client.portal.call(ctx.deepscan.tick)
    assert ctx.deepscan.store()["next"] == due  # still due, not moved to tomorrow


def test_a_scan_job_that_is_gone_doesnt_stop_the_schedule(client, ctx):
    ctx.deepscan.job_id = "gone"
    assert ctx.deepscan._running() is False


def test_installed_scanners_include_forge_only_when_its_rules_are_on_the_pc(ctx):
    assert ctx.deepscan.installed() == [deepscan.BEHAVIOUR]
    ctx.deepscan.forge_dir.mkdir(parents=True, exist_ok=True)
    (ctx.deepscan.forge_dir / deepscan.FORGE_FILE).write_text("")
    assert ctx.deepscan.installed() == [deepscan.BEHAVIOUR, deepscan.FORGE]


# ------------------------------------------------------------ who is told
def found_alert(core):
    core.db.insert(
        "app_alerts",
        {
            "server_id": "survival",
            "ts": 1.0,
            "event": "jar_scan_found",
            "title": "x",
            "body": "x",
            "page": "mods",
            "url": "/",
        },
    )


def test_known_bad_alerts_reach_moderators_with_deep_scan_only(multi_client, owner):  # noqa: F811
    sam = add_helper(multi_client, owner)
    core = multi_client.app.state.core
    found_alert(core)

    def events():
        answer = multi_client.get("/api/alerts?after=0", headers=sam).json()["alerts"]
        return [a["event"] for a in answer if a.get("server_id") == "survival"]

    give(multi_client, owner, {"survival": P.RUN_IT})
    assert events() == []
    assert not core.auth.may_see_alert("sam", "survival", "jar_scan_found")
    assert core.auth.may_see_alert("sam", "survival", "server_crashed")
    give(multi_client, owner, {"survival": P.RUN_IT | {P.DEEP_SCAN}})
    assert events() == ["jar_scan_found"]
    assert core.auth.may_see_alert("sam", "survival", "jar_scan_found")


class FakeBot:
    def __init__(self):
        self.told: list[str] = []

    def posts_alert(self, event):
        return False

    async def tell_owner(self, words):
        self.told.append(words)
        return True


def test_known_bad_is_sent_to_the_owner_on_discord(multi_client):
    core = multi_client.app.state.core
    bot = FakeBot()
    core.notifier.bot = lambda: bot
    send(core, Event(type="jar_scan_found", level="error", message="found x", server_id="survival"))
    send(core, Event(type="server_crashed", level="error", message="boom", server_id="survival"))
    assert len(bot.told) == 1 and "found x" in bot.told[0]


def test_the_bot_finds_the_owner_of_its_application():
    from agent.discord.bot import Bot

    bot = Bot.__new__(Bot)
    bot.app = {"owner": {"id": "42"}}
    assert bot.owner_id() == "42"
    bot.app = {"owner": {"id": "42"}, "team": {"owner_user_id": "7"}}
    assert bot.owner_id() == "7"
    bot.app = {}
    assert bot.owner_id() is None
