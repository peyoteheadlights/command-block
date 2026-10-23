"""Alerts per device: each sign-in follows the shared
alert list until it chooses its own, and then only its own reach it."""

from agent.events import Event

from .test_devices import sign_in
from .test_phone_app import crash, google, register_phone, send, sends  # noqa: F401


def other_device(client):
    """A second sign-in for the owner: another PC or phone."""
    return sign_in(client)[1]


def choose(client, headers, own, events=()):
    response = client.put(
        "/api/account/alerts", json={"own": own, "events": list(events)}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def queue(client, event):
    """On the agent's own event loop, where its alert sender runs."""
    notifier = client.app.state.core.notifier
    client.portal.call(notifier.handle, event)
    client.portal.call(notifier.drain)


def test_a_device_follows_the_shared_list_until_it_chooses(multi_client):
    found = multi_client.get("/api/account/alerts").json()
    assert found["own"] is False
    assert found["events"]["server_crashed"] is True
    assert found["events"]["player_left"] is False  # off in the shared list


def test_a_device_with_its_own_list_lists_only_those(multi_client):
    core = multi_client.app.state.core
    phone = other_device(multi_client)
    chosen = choose(multi_client, phone, True, ["player_joined"])
    assert chosen["own"] is True and chosen["events"]["server_crashed"] is False
    start = multi_client.get("/api/alerts").json()["latest"]
    send(core, crash())
    send(core, Event(type="player_joined", level="info", message="Steve", server_id="survival"))
    mine = multi_client.get("/api/alerts", params={"after": start}).json()["alerts"]
    theirs = multi_client.get("/api/alerts", params={"after": start}, headers=phone).json()
    assert [a["event"] for a in mine] == ["server_crashed", "player_joined"]
    assert [a["event"] for a in theirs["alerts"]] == ["player_joined"]


def test_an_alert_off_in_the_shared_list_still_reaches_a_device_that_chose_it(multi_client):
    core = multi_client.app.state.core
    phone = other_device(multi_client)
    choose(multi_client, phone, True, ["player_left"])
    queue(
        multi_client, Event(type="player_left", level="info", message="Steve", server_id="survival")
    )
    [row] = core.db.query("SELECT event FROM app_alerts")
    assert row["event"] == "player_left"
    # Discord, email and browser alerts follow the shared list: none sent.
    assert core.db.query("SELECT * FROM notifications_log WHERE event = 'player_left'") == []


def test_back_to_the_shared_list(multi_client):
    phone = other_device(multi_client)
    choose(multi_client, phone, True, [])
    back = choose(multi_client, phone, False)
    assert back["own"] is False and back["events"]["server_crashed"] is True


def test_an_unknown_alert_is_refused(multi_client):
    response = multi_client.put("/api/account/alerts", json={"own": True, "events": ["nope"]})
    assert response.status_code == 400


def test_a_phone_is_only_woken_for_its_own_choice(multi_client, google):  # noqa: F811
    core = multi_client.app.state.core
    register_phone(multi_client)
    choose(multi_client, dict(multi_client.headers), True, ["backup_failed"])
    send(core, crash())
    assert sends(google["calls"]) == []
    send(core, Event(type="backup_failed", level="error", message="x", server_id="survival"))
    assert len(sends(google["calls"])) == 1


def test_the_choice_survives_a_new_session_key(multi_client):
    choose(multi_client, dict(multi_client.headers), True, ["backup_failed"])
    token = multi_client.post("/api/auth/rotate").json()["token"]
    multi_client.headers["Authorization"] = f"Bearer {token}"
    assert multi_client.get("/api/account/alerts").json()["own"] is True
