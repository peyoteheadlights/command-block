"""Rented servers on a host's Pterodactyl panel, driven through the agent
API like the apps do, against a fake panel (tests/fake_panel.py)."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from agent import downloads, rented
from agent.security import permissions as P

from .fake_panel import ADDRESS, HOST, KEY, FakePanel
from .test_helpers import add_helper


@pytest.fixture
def panel(monkeypatch):
    fake = FakePanel()
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(fake.handle))
    monkeypatch.setattr(rented, "CONNECT", fake.connect)
    return fake


@pytest.fixture
def owner(multi_client):
    return dict(multi_client.headers)


def connect(client, servers=("abcd1234",), **extra):
    body = {"address": ADDRESS, "key": KEY, "allow_host": True, "servers": list(servers), **extra}
    return client.post("/api/rented/panels", json=body)


def only(client) -> dict:
    rows = client.get("/api/rented").json()["servers"]
    assert len(rows) == 1
    return rows[0]


def poll(client) -> None:
    core = client.app.state.core
    client.portal.call(core.rented.poll_all)


def test_the_owners_click_is_needed_before_the_panel_is_called(multi_client, panel):
    body = {"address": ADDRESS, "key": KEY}
    response = multi_client.post("/api/rented/check", json=body)
    assert response.status_code == 400
    assert panel.calls == []
    with pytest.raises(downloads.DownloadError):
        downloads.check_url(f"{ADDRESS}/api/client")  # not allowed outside a panel call
    with downloads.allow_panel(HOST):
        downloads.check_url(f"{ADDRESS}/api/client/servers/x/resources")
        with pytest.raises(downloads.DownloadError):
            downloads.check_url(f"{ADDRESS}/admin")  # only the client API
        with pytest.raises(downloads.DownloadError):
            downloads.check_url(f"http://{HOST}/api/client")


@pytest.mark.parametrize(
    "address", ["http://panel.example.com", "https://panel.example.com/server/abc", "nope", ""]
)
def test_only_a_plain_https_panel_address(multi_client, panel, address):
    body = {"address": address, "key": KEY, "allow_host": True}
    response = multi_client.post("/api/rented/check", json=body)
    assert response.status_code == 400


def test_connect_lists_the_panels_servers_and_adds_the_picked_ones(multi_client, panel):
    check = multi_client.post(
        "/api/rented/check", json={"address": "panel.example.com/", "key": KEY, "allow_host": True}
    ).json()
    assert check["address"] == ADDRESS
    assert [s["name"] for s in check["servers"]] == ["Survival", "Creative"]
    assert check["full_account"] is False  # a sub-user's key
    response = connect(multi_client)
    assert response.status_code == 200, response.text
    row = only(multi_client)
    assert row["name"] == "Survival"
    assert row["host"] == HOST
    assert row["kind"] == "pterodactyl"
    assert row["state"] == "OFFLINE"  # asked straight away
    assert row["resources"]["memory_bytes"] == 1_500_000_000
    assert "backups_here" in row["needs_this_pc"]
    assert "files" in row["later"]
    # Connecting again doesn't add it twice.
    connect(multi_client)
    assert len(multi_client.get("/api/rented").json()["servers"]) == 1


def test_a_main_account_key_is_pointed_out(multi_client, panel):
    panel.servers["abcd1234"]["owner"] = True
    check = multi_client.post(
        "/api/rented/check", json={"address": ADDRESS, "key": KEY, "allow_host": True}
    ).json()
    assert check["full_account"] is True


def test_the_key_is_never_in_an_answer_or_the_list_file(multi_client, panel):
    connect(multi_client)
    core = multi_client.app.state.core
    for path in ("/api/rented", f"/api/rented/{only(multi_client)['id']}/status"):
        assert KEY not in multi_client.get(path).text
    assert KEY not in core.rented.list_path.read_text()
    assert core.rented.key_path.exists()


def test_a_wrong_key_says_so(multi_client, panel):
    body = {"address": ADDRESS, "key": "ptlc_wrong", "allow_host": True}
    response = multi_client.post("/api/rented/check", json=body)
    # 502, never 401: the apps would read 401 as "you were signed out".
    assert response.status_code == 502
    assert "refused the key" in response.json()["detail"]


def test_a_revoked_key_shows_unknown_never_the_last_status(multi_client, panel):
    connect(multi_client, ["ef567890"])
    assert only(multi_client)["state"] == "ONLINE"
    panel.key = "revoked"
    poll(multi_client)
    row = only(multi_client)
    assert row["state"] == "UNKNOWN"
    assert "refused the key" in row["state_reason"]
    assert row["resources"] is None


def test_a_panel_that_is_down_shows_unknown_and_polling_slows(multi_client, panel):
    connect(multi_client)
    core = multi_client.app.state.core
    panel.down = True
    poll(multi_client)
    assert only(multi_client)["state"] == "UNKNOWN"
    assert core.rented.wait == 2 * rented.POLL_SECONDS
    panel.down = False
    poll(multi_client)
    assert only(multi_client)["state"] == "OFFLINE"
    assert core.rented.wait == rented.POLL_SECONDS


def test_too_many_requests_waits_at_least_a_minute(multi_client, panel):
    connect(multi_client)
    panel.status = 429
    poll(multi_client)
    row = only(multi_client)
    assert row["state"] == "UNKNOWN"
    assert "slow down" in row["state_reason"]
    assert multi_client.app.state.core.rented.wait >= rented.SLOW_DOWN_WAIT


def test_start_stop_restart_and_kill_behind_a_confirm(multi_client, panel):
    connect(multi_client)
    sid = only(multi_client)["id"]
    for signal in ("start", "stop", "restart"):
        response = multi_client.post(f"/api/rented/{sid}/power", json={"signal": signal})
        assert response.status_code == 200, response.text
    refused = multi_client.post(f"/api/rented/{sid}/power", json={"signal": "kill"})
    assert refused.status_code == 400
    assert "world can be damaged" in refused.json()["detail"]
    assert ("POST", "/servers/abcd1234/power", {"signal": "kill"}) not in panel.calls
    sure = multi_client.post(f"/api/rented/{sid}/power", json={"signal": "kill", "confirm": True})
    assert sure.status_code == 200
    assert ("POST", "/servers/abcd1234/power", {"signal": "kill"}) in panel.calls
    assert (
        multi_client.post(f"/api/rented/{sid}/power", json={"signal": "explode"}).status_code == 400
    )


def test_a_missing_permission_on_the_key_is_explained(multi_client, panel):
    connect(multi_client)
    sid = only(multi_client)["id"]
    panel.refused.add("/servers/abcd1234/power")
    response = multi_client.post(f"/api/rented/{sid}/power", json={"signal": "start"})
    assert response.status_code == 502
    assert "missing a permission" in response.json()["detail"]


def test_started_and_stopped_alerts(multi_client, panel):
    connect(multi_client)
    core = multi_client.app.state.core
    seen = []

    async def watch(event):
        if event.type.startswith("rented_"):
            seen.append(event.type)

    core.bus.subscribe(watch)
    panel.servers["abcd1234"]["state"] = "running"
    poll(multi_client)
    panel.servers["abcd1234"]["state"] = "offline"
    poll(multi_client)
    panel.down = True
    poll(multi_client)
    assert seen == ["rented_started", "rented_stopped", "rented_unreachable"]
    from agent.notifications.dispatcher import EVENT_MAP

    assert {"rented_started", "rented_stopped", "rented_unreachable"} <= set(EVENT_MAP)


def test_console_through_the_panel_websocket_and_checked_commands(multi_client, panel):
    connect(multi_client, ["ef567890"])
    sid = only(multi_client)["id"]
    multi_client.get(f"/api/rented/{sid}/console")
    lines: list[str] = []
    for _ in range(50):
        found = multi_client.get(f"/api/rented/{sid}/console").json()
        lines = [x["text"] for x in found["lines"]]
        if lines:
            break
        time.sleep(0.02)
    assert lines == panel.console
    socket = panel.sockets[0]
    assert socket.origin == ADDRESS
    assert socket.sent[0] == {"event": "auth", "args": ["jwt-1"]}
    bad = multi_client.post(f"/api/rented/{sid}/command", json={"command": "say hi; rm -rf"})
    assert bad.status_code == 400
    good = multi_client.post(f"/api/rented/{sid}/command", json={"command": "say hello"})
    assert good.status_code == 200, good.text
    assert ("POST", "/servers/ef567890/command", {"command": "say hello"}) in panel.calls


def test_host_backups_list_and_make(multi_client, panel):
    connect(multi_client)
    sid = only(multi_client)["id"]
    found = multi_client.get(f"/api/rented/{sid}/backups").json()
    assert found["limit"] == 3
    assert found["backups"][0]["name"] == "Before the update"
    assert found["backups"][0]["succeeded"] is True
    assert multi_client.post(f"/api/rented/{sid}/backups").status_code == 200
    newest = multi_client.get(f"/api/rented/{sid}/backups").json()["backups"][-1]
    assert newest["succeeded"] is None  # still being made: not a failure


def test_moderators_use_their_permissions_and_never_see_the_key(multi_client, panel, owner):
    connect(multi_client)
    sid = only(multi_client)["id"]
    sam = add_helper(multi_client, owner)
    response = multi_client.put(
        "/api/accounts/sam",
        json={"grants": {sid: sorted(P.START_AND_STOP), "survival": sorted(P.START_AND_STOP)}},
        headers=owner,
    )
    assert response.status_code == 200, response.text
    multi_client.headers.pop("Authorization")
    listed = multi_client.get("/api/rented", headers=sam)
    assert [r["id"] for r in listed.json()["servers"]] == [sid]
    assert listed.json()["panels"] == []
    assert KEY not in listed.text
    assert multi_client.get(f"/api/rented/{sid}/status", headers=sam).status_code == 200
    started = multi_client.post(f"/api/rented/{sid}/power", json={"signal": "start"}, headers=sam)
    assert started.status_code == 200
    assert multi_client.get(f"/api/rented/{sid}/console", headers=sam).status_code == 403
    assert multi_client.post(f"/api/rented/{sid}/backups", headers=sam).status_code == 403
    body = {"address": ADDRESS, "key": KEY, "allow_host": True}
    assert multi_client.post("/api/rented/check", json=body, headers=sam).status_code == 403
    accounts = multi_client.get("/api/accounts", headers=owner).json()
    assert sid in [s["id"] for s in accounts["servers"]]


def test_a_moderator_without_the_rented_server_cant_see_it(multi_client, panel, owner):
    connect(multi_client)
    sid = only(multi_client)["id"]
    sam = add_helper(multi_client, owner, servers=["survival"])
    multi_client.headers.pop("Authorization")
    assert multi_client.get("/api/rented", headers=sam).json()["servers"] == []
    assert multi_client.get(f"/api/rented/{sid}/status", headers=sam).status_code == 403


def test_remove_key_deletes_it_and_takes_the_servers_off(multi_client, panel):
    connect(multi_client, ["abcd1234", "ef567890"])
    core = multi_client.app.state.core
    panel_id = multi_client.get("/api/rented").json()["panels"][0]["id"]
    response = multi_client.delete(f"/api/rented/panels/{panel_id}")
    assert response.status_code == 200
    assert len(response.json()["removed"]) == 2
    assert multi_client.get("/api/rented").json() == {"servers": [], "panels": []}
    assert KEY.encode() not in core.rented.key_path.read_bytes()  # encrypted on Windows
    assert multi_client.delete(f"/api/rented/panels/{panel_id}").status_code == 404


def test_rented_servers_come_back_after_a_restart(multi_client, panel):
    connect(multi_client)
    core = multi_client.app.state.core
    again = rented.RentedServers(core)
    (server,) = again.servers.values()
    assert server.name == "Survival"
    assert server.state == "UNKNOWN"  # never a remembered status
    assert json.loads(core.rented.list_path.read_text())["panels"]


def test_removing_the_key_while_a_status_check_runs_is_safe(multi_client, panel):
    connect(multi_client)
    core = multi_client.app.state.core
    (server,) = core.rented.servers.values()
    multi_client.delete(f"/api/rented/panels/{server.panel_id}")
    assert multi_client.portal.call(core.rented.poll_one, server) is None
