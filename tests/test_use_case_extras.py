"""Test servers, New season, bringing servers back from a backup folder,
backup when the server stops, command buttons and the players-by-hour graph.
Driven through the agent like the apps do, with the fake Minecraft server
and a temporary backup folder. No network."""

from __future__ import annotations

import json
import time
import zipfile

import httpx
import pytest

from agent import bringin, downloads, duplicate, playerhours, seasons
from agent.core import AgentCore
from agent.jobs import RUNNING, SUCCEEDED
from agent.minecraft.state import ExitReason

from .conftest import build_multi_config
from .test_discord import fake  # noqa: F401
from .test_multi_server import wait_for
from .test_server_types import MC, fake_sources


@pytest.fixture
async def core(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(fake_sources()))
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


async def finish(core, job):
    assert await wait_for(lambda: core.jobs.get(job.id).state != RUNNING, 30)
    return core.jobs.get(job.id)


def fabric_mod(path, mod_id="mybuild"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "fabric.mod.json", json.dumps({"schemaVersion": 1, "id": mod_id, "version": "1"})
        )
    return path


def plugin(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("plugin.yml", "name: P\nversion: 1\nmain: a.B\n")
    return path


def make_test(ctx):
    ctx.config.set("server.test", True)


# ---------------------------------------------------------------- Test server
async def test_a_test_server_gets_no_schedules_and_discord_never_sees_it(core):
    ctx = core.servers["creative"]
    make_test(ctx)
    core.db.execute("DELETE FROM schedules WHERE server_id = 'creative'")
    ctx.scheduler.seed_defaults()
    assert not core.db.query("SELECT id FROM schedules WHERE server_id = 'creative'")
    assert set(core.discord_servers) == {"survival"}
    assert ctx.summary()["test"] is True and core.servers["survival"].summary()["test"] is False


async def test_the_build_folder_jars_are_copied_in_at_start_and_nothing_else(core, tmp_path):
    from agent.security.paths import PathSafetyError
    from agent.testserver import check_build_folder

    ctx = core.servers["creative"]
    build = tmp_path / "build" / "libs"
    build.mkdir(parents=True)
    with pytest.raises(PathSafetyError, match="Test server"):
        check_build_folder(ctx, str(build))
    make_test(ctx)
    with pytest.raises(PathSafetyError, match="overlaps"):
        check_build_folder(ctx, str(ctx.config.server_dir / "mods"))
    ctx.config.set("server.build_folder", check_build_folder(ctx, str(build)))
    fabric_mod(build / "mybuild-1.0.jar")
    (build / "run-me.bat").write_text("echo hi")
    await ctx.server.start()
    assert await ctx.server.wait_online(20)
    mods = ctx.config.mods_dir
    assert (mods / "mybuild-1.0.jar").read_bytes() == (build / "mybuild-1.0.jar").read_bytes()
    assert not (mods / "run-me.bat").exists()
    await ctx.server.stop()
    # A newer build is copied on the next start; a plugin can't go on Fabric.
    fabric_mod(build / "mybuild-1.0.jar", "mybuild2")
    plugin(build / "wrong.jar")
    await ctx.server.start()
    assert await ctx.server.wait_online(20)
    assert not (mods / "wrong.jar").exists()
    assert any("build folder not copied" in line.raw for line in ctx.server.console.tail(200))


def test_the_build_folder_is_checked_when_saved(multi_client, tmp_path):
    core = multi_client.app.state.core
    make_test(core.servers["creative"])
    build = tmp_path / "build"
    build.mkdir()

    def save(folder):
        return multi_client.put(
            "/api/servers/creative/settings",
            json={"updates": {"server.build_folder": str(folder)}},
        ).json()

    assert "overlaps" in save(tmp_path)["rejected"]["server.build_folder"]
    assert save(build)["applied"]["server.build_folder"] == str(build.resolve())


# ---------------------------------------------------------------- New season
async def test_a_new_season_keeps_the_old_world_forever_and_undo_brings_it_back(core):
    ctx = core.servers["survival"]
    base = ctx.config.server_dir
    result = await seasons.new_season(ctx, seed="1234", announce=False)
    assert not (base / "world").exists()
    assert "level-seed=1234" in (base / "server.properties").read_text()
    assert (base / "mods").is_dir(), "settings and add-ons stay"
    season = ctx.backups.get(result["safety_backup"]["id"])
    assert season["kind"] == "season"
    # The clean-up never deletes it, even with nothing to keep.
    ctx.config.set("backups.keep_daily", 0)
    core.db.execute(
        "UPDATE backups SET created_at = ? WHERE id = ?", (time.time() - 90 * 86400, season["id"])
    )
    await ctx.backups.apply_retention()
    assert ctx.backups.get(season["id"])
    await ctx.backups.changes.undo(user="admin")
    assert (base / "world" / "level.dat").read_bytes() == b"x" * 2048


async def test_an_old_season_opens_again_through_duplicate(core, tmp_path):
    ctx = core.servers["survival"]
    result = await seasons.new_season(ctx, seed="", announce=True)
    job = await duplicate.start_duplicate(
        core,
        ctx,
        name="Season one",
        directory=str(tmp_path / "s1"),
        world="fresh",
        season_backup=result["safety_backup"]["id"],
    )
    done = await finish(core, job)
    assert done.state == SUCCEEDED, done.message
    assert (tmp_path / "s1" / "world" / "level.dat").read_bytes() == b"x" * 2048
    assert not (ctx.config.server_dir / "world").exists(), "the live server stays new"


async def test_a_season_needs_a_world_and_a_sensible_seed(core):
    ctx = core.servers["survival"]
    with pytest.raises(seasons.SeasonError):
        await seasons.new_season(ctx, seed="a\nb", announce=False)
    import shutil

    for name in ("world", "world_nether", "world_the_end"):
        shutil.rmtree(ctx.config.server_dir / name, ignore_errors=True)
    with pytest.raises(seasons.SeasonError, match="no world"):
        await seasons.new_season(ctx, seed="", announce=False)


# ---------------------------------------------------------------- bringing servers back
async def test_servers_come_back_from_the_backup_folder_which_is_only_read(core, tmp_path):
    usb = tmp_path / "usb"
    usb.mkdir()
    ctx = core.servers["survival"]
    (ctx.config.server_dir / "whitelist.json").write_text('[{"name": "Sam"}]')
    ctx.config.set("backups.offsite_directory", str(usb))
    ctx.server.mc_version = MC
    await ctx.backups.create(kind="manual")
    before = {p: p.stat().st_mtime_ns for p in usb.rglob("*")}

    found = bringin.backup_folder(core, str(usb))
    row = next(r for r in found if r["id"] == "survival")
    assert row["name"] == "Survival" and row["type"] == "fabric" and row["minecraft"] == MC
    assert row["here"] and not row.get("problem")

    job = await bringin.bring_back(
        core,
        folder=str(usb),
        server="survival",
        name="Survival back",
        directory=str(tmp_path / "back"),
        eula_accepted=True,
    )
    done = await finish(core, job)
    assert done.state == SUCCEEDED, done.message
    back = tmp_path / "back"
    assert (back / "world" / "level.dat").read_bytes() == b"x" * 2048
    assert "Sam" in (back / "whitelist.json").read_text()
    assert (back / "eula.txt").read_text().strip().endswith("eula=true")
    assert done.result["port_note"], "its port was taken, so it got another with a note"
    assert {p: p.stat().st_mtime_ns for p in usb.rglob("*")} == before


async def test_bringing_back_needs_the_eula_and_says_why_one_cant(core, tmp_path):
    usb = tmp_path / "usb" / "Command Block" / "old"
    usb.mkdir(parents=True)
    (usb / "manual-1.zip").write_bytes(b"PK")
    row = bringin.backup_folder(core, str(tmp_path / "usb"))[0]
    assert "older version" in row["problem"]
    with pytest.raises(bringin.BringInError, match="EULA"):
        await bringin.bring_back(
            core,
            folder=str(tmp_path / "usb"),
            server="old",
            name="x",
            directory=str(tmp_path / "x"),
        )


# ---------------------------------------------------------------- backup on stop
async def test_backup_on_stop_only_after_play_and_a_planned_stop(core):
    from agent.events import Event

    ctx = core.servers["survival"]

    def stopped(**data):
        return Event(type="server_stopped", message="", data={"reason": "user_stop", **data})

    joined = lambda: core.db.insert(  # noqa: E731
        "player_sessions",
        {"server_id": "survival", "username": "Sam", "joined_at": time.time(), "left_at": None},
    )
    joined()
    assert not ctx.backup_on_stop(stopped()), "off by default"
    ctx.config.set("backups.on_stop", True)
    assert ctx.backup_on_stop(stopped())
    assert not ctx.backup_on_stop(stopped(reason=ExitReason.CRASH.value))
    assert not ctx.backup_on_stop(stopped(restart=True))
    ctx.server.held_by = "Restore"
    assert not ctx.backup_on_stop(stopped())
    ctx.server.held_by = None
    await ctx.handle(stopped())
    assert await wait_for(lambda: ctx._stop_backup is not None and ctx._stop_backup.done(), 20)
    assert [b["kind"] for b in ctx.backups.list_backups()] == ["scheduled"]
    assert not ctx.backup_on_stop(stopped()), "nobody played since that backup"


# ---------------------------------------------------------------- command buttons
def test_command_buttons_are_saved_checked_and_pressed_like_typed_ones(multi_client):
    base = "/api/servers/survival"
    saved = multi_client.put(
        f"{base}/command-buttons",
        json={
            "buttons": [
                {"name": "Day", "command": "time set day"},
                {"name": "Stop", "command": "stop"},
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    buttons = multi_client.get(f"{base}/command-buttons").json()
    assert [b["name"] for b in buttons["buttons"]] == ["Day", "Stop"] and buttons["max"] == 12
    assert buttons["buttons"][1]["danger_reason"], "a dangerous one is asked first"
    bad = multi_client.put(
        f"{base}/command-buttons", json={"buttons": [{"name": "X", "command": "say hi; rm -rf"}]}
    )
    assert bad.status_code == 400
    too_many = [{"name": f"b{i}", "command": "list"} for i in range(13)]
    assert multi_client.put(f"{base}/command-buttons", json={"buttons": too_many}).status_code in (
        400,
        422,
    )
    assert multi_client.get("/api/servers/creative/command-buttons").json()["buttons"] == []


# ---------------------------------------------------------------- players by hour
def test_hours_are_measured_peaks_and_unknown_hours_stay_unknown(core):
    now = time.time()
    peak = playerhours.HourPeak(core.db, "survival")
    hour_start = (now // 3600) * 3600
    for count in (1, 3, 2):
        peak.note(count, hour_start + 10)
    peak.note(0, hour_start - 3600 + 10)
    data = playerhours.profile(core.db, "survival", now)
    this = time.localtime(hour_start).tm_hour
    before = time.localtime(hour_start - 3600).tm_hour
    by_hour = {h["hour"]: h for h in data["hours"]}
    assert by_hour[this]["busiest"] == 3 and by_hour[before]["average"] == 0
    unknown = [h for h in data["hours"] if h["hour"] not in (this, before)]
    assert all(h["average"] is None for h in unknown)
    assert data["quietest_hour"] == before and data["measured_hours"] == 2
    assert playerhours.profile(core.db, "nobody", now)["quietest_hour"] is None


def test_hours_are_in_the_api(multi_client):
    data = multi_client.get("/api/servers/survival/players/hours").json()
    assert len(data["hours"]) == 24 and data["days"] == 28


def test_the_discord_bot_does_not_list_a_test_server(fake, tmp_path, monkeypatch):  # noqa: F811
    from tests.fake_discord import wait_for as wait_sync
    from tests.test_discord import signed_in

    config = build_multi_config(tmp_path)
    config.set_server_value("creative", "server.test", True)
    client = signed_in(config, monkeypatch)
    try:
        status = wait_sync(
            lambda: (s := client.get("/api/discord").json())["state"] == "connected" and s
        )
        assert [s["id"] for s in status["servers"]] == ["survival"]
    finally:
        client.__exit__(None, None, None)
