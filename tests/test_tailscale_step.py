"""The agent's side of the desktop app's Tailscale step: moving onto the
Tailscale address only once Tailscale says it's connected (even with the
app closed), and the minute check after that. Tailscale itself is faked."""

import time

from agent import tailscale_watch

from .conftest import PASSWORD


def signed_in(client):
    token = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    client.headers["Authorization"] = f"Bearer {token.json()['token']}"
    return client


def tailscale_says(monkeypatch, connected, address="100.101.102.103", error=None):
    report = {
        "cli_found": True,
        "connected": connected,
        "backend_state": "Running" if connected else "NeedsLogin",
        "addresses": [address, "fd7a:115c:a1e0::1"] if connected else [],
        "dns_name": "minecraft-pc.tail1234.ts.net" if connected else None,
        "account": "jo@example.com" if connected else None,
        "error": error,
    }
    monkeypatch.setattr(tailscale_watch, "tailscale_status", lambda: report)


def alerts(client):
    return [a["event"] for a in client.get("/api/alerts?after=0").json()["alerts"]]


def test_the_agent_moves_only_once_tailscale_says_connected(client, monkeypatch):
    signed_in(client)
    core = client.app.state.core
    restarts = []
    core.request_restart = lambda: restarts.append(True)
    tailscale_says(monkeypatch, connected=False)
    state = client.post("/api/tailscale/move").json()
    assert state["moving"] is True and state["connected"] is False
    assert state["moved"] is False and core.config.network.host == "127.0.0.1"
    # The app was closed: the minute check finishes the move by itself.
    tailscale_says(monkeypatch, connected=True)
    client.portal.call(core.tailscale.tick)
    assert core.config.network.host == "100.101.102.103"
    assert restarts == [True]  # to listen on the new address
    client.portal.call(core.notifier.drain)
    assert "phones_can_reach" in alerts(client)
    state = client.get("/api/tailscale").json()
    assert state["moved"] is True and state["moving"] is False
    assert state["restarting"] is True  # the desktop app waits for it to come back
    assert state["account"] == "jo@example.com"


def test_nothing_moves_or_is_asked_without_the_step(client, monkeypatch):
    signed_in(client)
    core = client.app.state.core
    asked = []
    monkeypatch.setattr(tailscale_watch, "tailscale_status", lambda: asked.append(1) or {})
    client.portal.call(core.tailscale.tick)
    assert asked == []  # weak PCs: no Tailscale command every minute for nothing
    assert core.config.network.host == "127.0.0.1"


def test_an_address_set_by_hand_is_never_moved_or_watched(client, monkeypatch):
    """A LAN address in config.yaml isn't Tailscale's: no move, no alert."""
    signed_in(client)
    core = client.app.state.core
    core.config.set("network.host", "192.168.1.20")
    asked = []
    monkeypatch.setattr(tailscale_watch, "tailscale_status", lambda: asked.append(1) or {})
    client.portal.call(core.tailscale.tick)
    assert asked == [] and core.config.network.host == "192.168.1.20"


def test_a_move_left_half_way_stops_hiding_tailscale_stopping(client, monkeypatch):
    """Signed out for a different account, then the sign-in was never
    finished: after a while it counts as stopped again."""
    signed_in(client)
    core = client.app.state.core
    core.config.set("network.host", "100.101.102.103")
    core.tailscale._save(moving=time.time() - tailscale_watch.MOVING_FOR - 1)
    tailscale_says(monkeypatch, connected=False)
    client.portal.call(core.tailscale.tick)
    client.portal.call(core.notifier.drain)
    assert alerts(client).count("tailscale_stopped") == 1


def test_tailscale_stopping_raises_the_alert_and_the_suggestion_once(client, monkeypatch):
    signed_in(client)
    core = client.app.state.core
    core.config.set("network.host", "100.101.102.103")
    tailscale_says(monkeypatch, connected=False)
    client.portal.call(core.tailscale.tick)
    client.portal.call(core.tailscale.tick)
    client.portal.call(core.notifier.drain)
    assert alerts(client).count("tailscale_stopped") == 1
    found = client.get("/api/servers/test/recommendations").json()["recommendations"]
    [stopped] = [r for r in found if r["id"] == "tailscale_stopped"]
    assert stopped["title"] == "Phones can't reach this PC: Tailscale stopped"
    assert stopped["action"]["label"] == "Reconnect"
    tailscale_says(monkeypatch, connected=True)
    client.portal.call(core.tailscale.tick)
    found = client.get("/api/servers/test/recommendations").json()["recommendations"]
    assert "tailscale_stopped" not in [r["id"] for r in found]


def test_unknown_isnt_stopped(client, monkeypatch):
    """The CLI couldn't be asked: that's not proof Tailscale stopped."""
    signed_in(client)
    core = client.app.state.core
    core.config.set("network.host", "100.101.102.103")
    monkeypatch.setattr(
        tailscale_watch, "tailscale_status", lambda: {"connected": None, "error": "timed out"}
    )
    client.portal.call(core.tailscale.tick)
    client.portal.call(core.notifier.drain)
    assert "tailscale_stopped" not in alerts(client)


def test_skipping_adds_the_checklist_item(client, monkeypatch):
    signed_in(client)
    before = client.get("/api/servers/test/getting-started").json()
    assert "phones" not in [i["id"] for i in before["items"]]
    state = client.post("/api/tailscale/skip").json()
    assert state["skipped"] is True
    after = client.get("/api/servers/test/getting-started").json()
    [item] = [i for i in after["items"] if i["id"] == "phones"]
    assert item["done"] is False


def test_only_the_owner_runs_the_step(client):
    assert client.post("/api/tailscale/move").status_code == 401


def test_a_different_account_moves_onto_its_new_address(client, monkeypatch):
    signed_in(client)
    core = client.app.state.core
    restarts = []
    core.request_restart = lambda: restarts.append(True)
    core.config.set("network.host", "100.101.102.103")
    tailscale_says(monkeypatch, connected=True, address="100.64.0.9")
    client.portal.call(core.tailscale.tick)
    assert core.config.network.host == "100.64.0.9"  # the new pairing link has it
    assert restarts == [True]


def test_the_same_account_again_ends_the_step(client, monkeypatch):
    signed_in(client)
    core = client.app.state.core
    core.config.set("network.host", "100.101.102.103")
    tailscale_says(monkeypatch, connected=True)
    state = client.post("/api/tailscale/move").json()
    assert state["moving"] is False and state["moved"] is True
    assert state["restarting"] is False


def test_no_move_while_https_is_off(client, monkeypatch):
    """main.py refuses plain HTTP on anything but 127.0.0.1, so moving
    would leave the agent unable to start."""
    signed_in(client)
    core = client.app.state.core
    core.config.set("tls.enabled", False)
    tailscale_says(monkeypatch, connected=True)
    answer = client.post("/api/tailscale/move")
    assert answer.status_code == 409 and "HTTPS" in answer.json()["detail"]
    assert core.config.network.host == "127.0.0.1"
    assert client.get("/api/tailscale").json()["moving"] is False


def test_the_signed_in_account_is_read_from_tailscale(monkeypatch):
    import json
    import subprocess

    from agent import tailscale

    status = {
        "BackendState": "Running",
        "Self": {"DNSName": "pc.tail1.ts.net.", "TailscaleIPs": ["100.64.0.9"], "UserID": 7},
        "User": {"7": {"LoginName": "jo@example.com"}},
    }
    monkeypatch.setattr(tailscale, "tailscale_binary", lambda: "tailscale")
    monkeypatch.setattr(
        tailscale.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(status), ""),
    )
    assert tailscale.tailscale_status()["account"] == "jo@example.com"


def test_signing_out_to_switch_accounts_isnt_an_alert(client, monkeypatch):
    signed_in(client)
    core = client.app.state.core
    core.config.set("network.host", "100.101.102.103")
    tailscale_says(monkeypatch, connected=False)
    client.post("/api/tailscale/move")  # "Use a different account" asks first
    client.portal.call(core.tailscale.tick)
    client.portal.call(core.notifier.drain)
    assert "tailscale_stopped" not in alerts(client)
