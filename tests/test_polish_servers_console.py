# ruff: noqa: F811  (the owner fixture imported from test_helpers is used by name)
"""All servers' order, groups, Start all and Stop all; the game rules
screen; the console's command list; and older logs."""

from __future__ import annotations

import gzip

from agent import gamerules
from agent.minecraft.ask import parse_help

from .test_bedrock import bedrock_client  # noqa: F401
from .test_helpers import add_helper, owner  # noqa: F401
from .test_multi_server import wait_for_state


def ids(client, headers=None) -> list[str]:
    return [s["id"] for s in client.get("/api/servers", headers=headers).json()["servers"]]


# ---------------------------------------------------------------- the list
def test_the_order_and_groups_are_kept_for_every_app(multi_client):
    assert ids(multi_client) == ["survival", "creative"]
    assert multi_client.put("/api/servers/order", json={"ids": ["creative"]}).status_code == 200
    # A server the app didn't send stays on the list, after the ones it did.
    assert ids(multi_client) == ["creative", "survival"]

    saved = multi_client.put("/api/servers/survival/group", json={"group": "  Friends   club "})
    assert saved.json() == {"ok": True, "group": "Friends club"}
    rows = {s["id"]: s for s in multi_client.get("/api/servers").json()["servers"]}
    assert rows["survival"]["group"] == "Friends club" and rows["creative"]["group"] == ""
    assert multi_client.put("/api/servers/survival/group", json={"group": ""}).status_code == 200
    assert multi_client.get("/api/servers").json()["servers"][1]["group"] == ""


def test_an_unknown_server_in_the_order_is_refused(multi_client):
    response = multi_client.put("/api/servers/order", json={"ids": ["creative", "nope"]})
    assert response.status_code == 404
    assert ids(multi_client) == ["survival", "creative"]


def test_a_moderator_cant_reorder_or_regroup(multi_client, owner):
    sam = add_helper(multi_client, owner, servers=["creative"])
    assert multi_client.put("/api/servers/order", json={"ids": []}, headers=sam).status_code == 403
    response = multi_client.put("/api/servers/creative/group", json={"group": "x"}, headers=sam)
    assert response.status_code == 403


def test_start_all_and_stop_all(multi_client):
    started = multi_client.post("/api/servers/start-all", json={}).json()["servers"]
    assert [(s["id"], s["result"]) for s in started] == [
        ("survival", "started"),
        ("creative", "started"),
    ]
    assert wait_for_state(multi_client, "survival", "ONLINE")
    assert wait_for_state(multi_client, "creative", "ONLINE")
    again = multi_client.post("/api/servers/start-all", json={"ids": ["creative"]}).json()
    assert again["servers"] == [
        {"id": "creative", "name": "Creative", "result": "already", "detail": None}
    ]

    stopped = multi_client.post("/api/servers/stop-all", json={}).json()["servers"]
    assert {s["id"] for s in stopped} == {"survival", "creative"}
    assert all(s["result"] in ("stopped", "stopping") for s in stopped)
    assert wait_for_state(multi_client, "survival", "OFFLINE")
    assert wait_for_state(multi_client, "creative", "OFFLINE")


def test_start_all_only_starts_what_the_account_may(multi_client, owner):
    sam = add_helper(multi_client, owner, servers=["creative"])
    try:
        started = multi_client.post("/api/servers/start-all", json={}, headers=sam).json()
        assert [s["id"] for s in started["servers"]] == ["creative"]
        assert multi_client.get("/api/servers/survival/status").json()["state"] == "OFFLINE"
    finally:
        multi_client.post("/api/servers/stop-all", json={})
        wait_for_state(multi_client, "creative", "OFFLINE")


# ---------------------------------------------------------------- game rules
def test_game_rules_need_a_running_server(multi_client):
    view = multi_client.get("/api/servers/survival/gamerules").json()
    assert view["running"] is False and view["rules"] == []
    assert "friendly" in view["presets"]
    response = multi_client.put("/api/servers/survival/gamerules", json={"preset": "friendly"})
    assert response.status_code == 409 and "Start the server" in response.json()["detail"]


def test_game_rules_are_read_from_the_server_and_changed_live(multi_client):
    assert multi_client.post("/api/servers/survival/server/start").status_code == 200
    assert wait_for_state(multi_client, "survival", "ONLINE")
    try:
        view = multi_client.get("/api/servers/survival/gamerules").json()
        assert view["rules"] == [
            {"name": "keepInventory", "value": False},
            {"name": "mobGriefing", "value": True},
            {"name": "spawnRadius", "value": 10},
        ]
        # The preset's rules this server doesn't list are skipped and said so.
        saved = multi_client.put("/api/servers/survival/gamerules", json={"preset": "friendly"})
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["before"] == {"keepInventory": False, "mobGriefing": True}
        assert sorted(body["skipped"]) == ["dofiretick", "doinsomnia"]
        values = {r["name"]: r["value"] for r in body["rules"]}
        assert values == {"keepInventory": True, "mobGriefing": False, "spawnRadius": 10}

        # Undo is the old values sent back; a number rule takes a number.
        undo = multi_client.put(
            "/api/servers/survival/gamerules", json={"rules": body["before"]}
        ).json()
        assert {r["name"]: r["value"] for r in undo["rules"]}["keepInventory"] is False
        wrong = multi_client.put(
            "/api/servers/survival/gamerules", json={"rules": {"spawnRadius": True}}
        )
        assert wrong.status_code == 409 and "whole number" in wrong.json()["detail"]
        assert multi_client.put(
            "/api/servers/survival/gamerules", json={"rules": {"spawnRadius": 3}}
        ).json()["before"] == {"spawnRadius": 10}

        commands = multi_client.get("/api/servers/survival/console/commands").json()["commands"]
        assert [c["name"] for c in commands] == ["gamerule", "help", "say", "stop"]
    finally:
        multi_client.post("/api/servers/survival/server/stop", json={})
        wait_for_state(multi_client, "survival", "OFFLINE")


def test_bedrock_answers_all_rules_on_one_line_and_help_in_pages(bedrock_client):
    assert bedrock_client.post("/api/servers/bedrock/server/start").status_code == 200
    assert wait_for_state(bedrock_client, "bedrock", "ONLINE")
    try:
        rules = bedrock_client.get("/api/servers/bedrock/gamerules").json()["rules"]
        assert {"name": "keepinventory", "value": False} in rules
        saved = bedrock_client.put(
            "/api/servers/bedrock/gamerules", json={"rules": {"keepInventory": True}}
        )
        assert saved.json()["before"] == {"keepinventory": False}
        commands = bedrock_client.get("/api/servers/bedrock/console/commands").json()["commands"]
        assert [c["name"] for c in commands] == ["gamerule", "say"]
    finally:
        bedrock_client.post("/api/servers/bedrock/server/stop", json={})
        wait_for_state(bedrock_client, "bedrock", "OFFLINE")


def test_rule_names_match_across_versions():
    assert gamerules.plain("keepInventory") == gamerules.plain("minecraft:keep_inventory")
    assert gamerules.plain("keepinventory") == "keepinventory"


def test_the_help_list_keeps_each_command_once():
    found = parse_help(["/tp <targets> <location>", "/tp <location>", "Unknown line", "/Say <msg>"])
    assert found == [
        {"name": "say", "usage": "/Say <msg>"},
        {"name": "tp", "usage": "/tp <targets> <location>"},
    ]


def test_the_command_list_is_empty_while_stopped(multi_client):
    assert multi_client.get("/api/servers/survival/console/commands").json() == {"commands": []}


# ---------------------------------------------------------------- older logs
def test_older_logs_are_listed_and_read_unzipped(multi_client, multi):
    logs = multi.for_server("survival").server_dir / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "latest.log").write_text("today\n", encoding="utf-8")
    with gzip.open(logs / "2026-11-19-1.log.gz", "wt", encoding="utf-8") as handle:
        handle.write("[12:00:00] [Server thread/INFO]: Done\nsecond line\n")
    (logs / "notes.txt").write_text("not a log", encoding="utf-8")

    files = multi_client.get("/api/servers/survival/logs/files").json()["files"]
    assert sorted(f["name"] for f in files) == ["2026-11-19-1.log.gz", "latest.log"]
    old = multi_client.get("/api/servers/survival/logs/files/2026-11-19-1.log.gz").json()
    assert old == {
        "name": "2026-11-19-1.log.gz",
        "lines": ["[12:00:00] [Server thread/INFO]: Done", "second line"],
        "cut": False,
    }


def test_only_log_files_in_the_logs_folder_can_be_read(multi_client, multi):
    folder = multi.for_server("survival").server_dir
    (folder / "logs").mkdir(exist_ok=True)
    for name in ("server.properties", "..%2Fserver.properties", "notes.txt", "missing.log"):
        response = multi_client.get(f"/api/servers/survival/logs/files/{name}")
        assert response.status_code == 404, name


def test_a_big_log_is_cut(multi_client, multi, monkeypatch):
    from agent import oldlogs

    monkeypatch.setattr(oldlogs, "MAX_LINES", 3)
    logs = multi.for_server("survival").server_dir / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "latest.log").write_text("1\n2\n3\n4\n5\n", encoding="utf-8")
    body = multi_client.get("/api/servers/survival/logs/files/latest.log").json()
    assert body["lines"] == ["1", "2", "3"] and body["cut"] is True


def test_a_server_with_no_logs_folder_has_no_older_logs(multi_client, multi):
    import shutil

    shutil.rmtree(multi.for_server("creative").server_dir / "logs", ignore_errors=True)
    assert multi_client.get("/api/servers/creative/logs/files").json() == {"files": []}
