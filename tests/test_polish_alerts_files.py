# ruff: noqa: F811  (the owner fixture imported from test_helpers is used by name)
"""CPU, memory and disk alerts (one when it starts, one when it ends),
ignored exit codes, lower priority, the graphs as CSV, and a server's
files: look, download, upload."""

from __future__ import annotations

import asyncio
import os

import psutil
import pytest

from agent.database.db import Database
from agent.events import EventBus
from agent.minecraft import cpu
from agent.minecraft.process import MinecraftServer
from agent.minecraft.state import ExitReason, ServerState
from agent.monitoring.metrics import MetricsMonitor, Stretch

from .test_helpers import add_helper, owner  # noqa: F401


# ---------------------------------------------------------------- alerts
def test_a_stretch_says_started_once_then_ended_once():
    stretch = Stretch()
    assert stretch.step(True, 0, wait=300) is None  # just started being high
    assert stretch.step(True, 299, wait=300) is None
    assert stretch.step(True, 300, wait=300) == "started"
    assert stretch.step(True, 900, wait=300) is None  # no repeats while it stays high
    assert stretch.step(False, 910, wait=300) == "ended"
    assert stretch.step(False, 920, wait=300) is None
    # A short spike never says anything.
    assert stretch.step(True, 1000, wait=300) is None
    assert stretch.step(False, 1010, wait=300) is None


@pytest.fixture
def parts(config):
    bus = EventBus()
    db = Database(config.database_path)
    db.register_server("test", "Test", str(config.server_dir))
    return config, bus, db, MinecraftServer(config, bus, db)


async def test_disk_alert_starts_and_ends_once(parts, monkeypatch):
    config, bus, db, server = parts
    config.set("thresholds.disk_free_gb", 20.0)
    seen = []
    bus.subscribe(lambda event: seen.append(event))
    metrics = MetricsMonitor(config, bus, db, server)
    free = {"gb": 5.0}
    real = metrics.snapshot

    def snapshot():
        return {**real(), "disk_free_gb": free["gb"]}

    monkeypatch.setattr(metrics, "snapshot", snapshot)
    for _ in range(3):
        await metrics.sample_once(player_count=0)
    free["gb"] = 50.0
    for _ in range(2):
        await metrics.sample_once(player_count=0)
    disk = [e for e in seen if e.type.startswith("low_disk")]
    assert [e.type for e in disk] == ["low_disk", "low_disk_ended"]
    assert disk[1].data == {"value": 50.0, "threshold": 20.0}
    assert "50.0 GB free" in disk[1].message


async def test_cpu_waits_until_it_stays_high_and_is_told_once_for_all_servers(
    parts, config, monkeypatch
):
    _config, bus, db, server = parts
    seen = []
    bus.subscribe(lambda event: seen.append(event.type))
    config.set("thresholds.cpu_percent", 50.0)
    config.set("thresholds.high_minutes", 5.0)
    clock = {"now": 1000.0}
    monkeypatch.setattr("agent.monitoring.metrics.time.time", lambda: clock["now"])
    first = MetricsMonitor(config, bus, db, server)
    second = MetricsMonitor(config, bus, db, server)
    for monitor in (first, second):
        monkeypatch.setattr(monitor, "snapshot", lambda: {**fake_sample(), "cpu_percent": 95.0})
    for minute in range(0, 7):
        clock["now"] = 1000.0 + minute * 60
        await first.sample_once(player_count=0)
        await second.sample_once(player_count=0)
    assert seen.count("high_cpu") == 1  # after 5 minutes, and once for both servers
    for monitor in (first, second):
        monkeypatch.setattr(monitor, "snapshot", lambda: {**fake_sample(), "cpu_percent": 10.0})
    await first.sample_once(player_count=0)
    await second.sample_once(player_count=0)
    assert seen.count("high_cpu_ended") == 1


def fake_sample() -> dict:
    return {
        "ts": 0.0,
        "cpu_percent": 10.0,
        "ram_used_mb": 1000.0,
        "ram_total_mb": 8000.0,
        "ram_percent": 12.5,
        "proc_ram_mb": None,
        "disk_free_gb": 100.0,
        "disk_percent": 10.0,
        "net_sent_mb_s": 0.0,
        "net_recv_mb_s": 0.0,
        "tps": None,
        "mspt": None,
    }


def test_the_ended_alerts_reach_every_app():
    import json
    from pathlib import Path

    from agent.notifications.dispatcher import ALERT_PAGES, EVENT_MAP

    root = Path(__file__).resolve().parent.parent
    feed = json.loads((root / "shared" / "feed.json").read_text(encoding="utf-8"))
    strings = json.loads((root / "shared" / "strings.json").read_text(encoding="utf-8"))
    from desktop.activity import FEED

    for kind in ("high_cpu_ended", "high_ram_ended", "low_disk_ended"):
        assert EVENT_MAP[kind][0] == kind.removesuffix("_ended")  # the same alert switch
        assert ALERT_PAGES[kind] == "performance"
        assert feed[kind] in strings  # the phones' feed
        assert FEED[kind] in strings  # the desktop app's Activity


# ---------------------------------------------------------------- exit codes, priority
def test_exit_codes_are_read_from_the_setting():
    assert cpu.exit_codes("") == set()
    assert cpu.exit_codes(" 1, 130;2 ") == {1, 2, 130}
    with pytest.raises(ValueError, match="'one'"):
        cpu.exit_codes("1, one")


async def test_an_ignored_exit_code_is_a_stop_not_a_crash(config):
    config.set("monitor.ignored_exit_codes", "1")
    bus = EventBus()
    seen = []
    bus.subscribe(lambda e: seen.append(e.type))
    server = MinecraftServer(config, bus)
    await server.start()
    assert await server.wait_online(timeout=15)
    await server.send_command("crash")  # the fake server exits with 1
    assert await server.wait_exit(timeout=15)
    await asyncio.sleep(0.2)
    assert server.state is ServerState.OFFLINE
    assert server.last_exit_reason is ExitReason.CLEAN_EXIT
    assert "server_crashed" not in seen and "server_stopped" in seen
    assert server.restart_at is None  # no automatic restart


async def test_low_priority_is_set_on_the_running_server(config):
    config.set("server.low_priority", True)
    server = MinecraftServer(config, EventBus())
    await server.start()
    try:
        assert await server.wait_online(timeout=15)
        nice = psutil.Process(server.pid).nice()
        if os.name == "nt":
            assert nice == psutil.BELOW_NORMAL_PRIORITY_CLASS  # type: ignore[attr-defined]
        else:
            assert nice >= 10
    finally:
        await server.stop()


def test_server_settings_check_the_exit_codes(multi_client):
    saved = multi_client.put(
        "/api/servers/survival/settings", json={"updates": {"monitor.ignored_exit_codes": "130,1"}}
    )
    assert saved.json()["applied"] == {"monitor.ignored_exit_codes": "1, 130"}
    refused = multi_client.put(
        "/api/servers/survival/settings", json={"updates": {"monitor.ignored_exit_codes": "x"}}
    )
    assert "isn't an exit code" in refused.json()["rejected"]["monitor.ignored_exit_codes"]
    on = multi_client.put(
        "/api/servers/survival/settings", json={"updates": {"server.low_priority": True}}
    )
    assert on.json()["applied"] == {"server.low_priority": True}


# ---------------------------------------------------------------- CSV
def test_the_graphs_come_as_csv(multi_client):
    import time

    ctx = multi_client.app.state.core.servers["survival"]
    ctx.metrics.db.insert(
        "metrics",
        {"server_id": ctx.server_id, "ts": time.time(), "cpu_percent": 12.0, "players": 2},
    )
    found = multi_client.get("/api/servers/survival/performance/csv?hours=1").json()
    assert found["filename"].endswith(".csv")
    lines = found["text"].splitlines()
    assert lines[0] == "time,pc_cpu_percent,pc_memory_mb,server_memory_mb,tps,mspt,players"
    row = lines[1].split(",")
    assert row[6] != ""  # players were counted
    assert row[4] == ""  # not measured: an empty cell, never 0


# ---------------------------------------------------------------- server files
def folder(client):
    return client.app.state.core.servers["survival"].config.server_dir


def test_the_server_folder_can_be_looked_through(multi_client):
    (folder(multi_client) / "plugins" / "Thing").mkdir(parents=True)
    (folder(multi_client) / "plugins" / "Thing" / "config.yml").write_bytes(b"a: 1\n")
    top = multi_client.get("/api/servers/survival/files").json()
    assert top["path"] == "" and top["parent"] is None
    names = [e["name"] for e in top["entries"]]
    assert "plugins" in names
    first_file = next(i for i, e in enumerate(top["entries"]) if not e["folder"])
    assert all(e["folder"] for e in top["entries"][:first_file])  # folders first
    inner = multi_client.get("/api/servers/survival/files?path=plugins/Thing").json()
    assert inner["parent"] == "plugins"
    assert inner["entries"] == [
        {
            "name": "config.yml",
            "folder": False,
            "size": 5,
            "changed": inner["entries"][0]["changed"],
        }
    ]


@pytest.mark.parametrize("path", ["..", "../..", "plugins/../../x", "C:/Windows", "/etc"])
def test_nothing_outside_the_server_folder(multi_client, path):
    assert multi_client.get(f"/api/servers/survival/files?path={path}").status_code == 400
    assert multi_client.get(f"/api/servers/survival/files/download?path={path}").status_code == 400


def test_a_link_out_of_the_server_folder_is_never_followed(multi_client, tmp_path):
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "key.txt").write_text("nope")
    try:
        os.symlink(outside, folder(multi_client) / "sneaky", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make links")
    listed = [e["name"] for e in multi_client.get("/api/servers/survival/files").json()["entries"]]
    assert "sneaky" not in listed
    assert multi_client.get("/api/servers/survival/files?path=sneaky").status_code == 400
    response = multi_client.get("/api/servers/survival/files/download?path=sneaky/key.txt")
    assert response.status_code == 400


def test_download_and_upload(multi_client):
    (folder(multi_client) / "notes.txt").write_text("hello")
    got = multi_client.get("/api/servers/survival/files/download?path=notes.txt")
    assert got.status_code == 200 and got.content == b"hello"
    assert got.headers["content-type"] == "application/octet-stream"

    (folder(multi_client) / "plugins").mkdir(exist_ok=True)
    sent = multi_client.post(
        "/api/servers/survival/files/upload?path=plugins",
        files={"file": ("extra.json", b"{}", "application/json")},
    )
    assert sent.json() == {
        "ok": True,
        "path": "plugins/extra.json",
        "name": "extra.json",
        "size": 2,
    }
    assert (folder(multi_client) / "plugins" / "extra.json").read_bytes() == b"{}"

    again = multi_client.post(
        "/api/servers/survival/files/upload?path=plugins",
        files={"file": ("extra.json", b"[1]", "application/json")},
    )
    assert again.status_code == 400 and "already" in again.json()["detail"]
    assert (folder(multi_client) / "plugins" / "extra.json").read_bytes() == b"{}"  # never replaced

    for name in ("run.bat", "evil.exe", "../x.txt"):
        refused = multi_client.post(
            "/api/servers/survival/files/upload", files={"file": (name, b"x", "text/plain")}
        )
        assert refused.status_code == 400, name
    assert not (folder(multi_client) / "run.bat").exists()


def test_uploads_and_downloads_have_size_limits(multi_client, monkeypatch):
    from agent import serverfiles

    monkeypatch.setattr(serverfiles, "MAX_UPLOAD_BYTES", 10)
    monkeypatch.setattr(serverfiles, "MAX_DOWNLOAD_BYTES", 3)
    big = multi_client.post(
        "/api/servers/survival/files/upload", files={"file": ("big.txt", b"x" * 11, "text/plain")}
    )
    assert big.status_code == 400 and not (folder(multi_client) / "big.txt").exists()
    (folder(multi_client) / "four.txt").write_text("four")
    assert multi_client.get("/api/servers/survival/files/download?path=four.txt").status_code == 400


def test_server_files_are_for_the_owner_only(multi_client, owner):
    sam = add_helper(multi_client, owner, servers=["survival"])
    for response in (
        multi_client.get("/api/servers/survival/files", headers=sam),
        multi_client.get("/api/servers/survival/files/download?path=x", headers=sam),
        multi_client.post(
            "/api/servers/survival/files/upload",
            files={"file": ("a.txt", b"x", "text/plain")},
            headers=sam,
        ),
    ):
        assert response.status_code == 403
