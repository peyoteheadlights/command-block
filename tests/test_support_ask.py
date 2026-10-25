"""The one gentle ask (agent/supportask.py): only after two weeks of real
use, never while something is wrong, gone for good once answered, and
never for a moderator."""

import asyncio
import time

import pytest

from agent import checklist, recommendations, supportask
from agent.events import Event

from .test_helpers import add_helper

DAY = 86400


@pytest.fixture
def client(multi_client):
    return multi_client  # signed in as the owner


def core_of(client):
    return client.app.state.core


def ask(client) -> dict:
    response = client.get("/api/support-ask")
    assert response.status_code == 200, response.text
    return response.json()


def started_days_ago(client, days: float) -> None:
    db = core_of(client).db
    saved = db.get_setting(supportask.STATE_KEY, {}) or {}
    db.set_setting(supportask.STATE_KEY, {**saved, "first_start": time.time() - days * DAY})


def a_tip(rec_id: str) -> recommendations.Recommendation:
    return recommendations.Recommendation(
        id=rec_id, title="t", reason="r", evidence="e", action={}, fingerprint=rec_id
    )


@pytest.fixture
def ready(client, monkeypatch):
    """Two weeks of use, set up, nothing wrong: the card shows."""
    core = core_of(client)
    for ctx in core.servers.values():
        checklist.dismiss(ctx)
    monkeypatch.setattr(recommendations, "evaluate", lambda facts: [])
    core.db.execute("DELETE FROM app_alerts")
    started_days_ago(client, 15)
    assert ask(client) == {"show": True, "reason": "ready"}
    return core


def test_not_without_a_measured_start(client):
    core = core_of(client)
    core.db.execute("DELETE FROM events WHERE type = 'server_started'")
    assert ask(client)["reason"] == "never_started"
    asyncio.run(supportask.handler(core.db)(Event(type="server_started", ts=1000.0)))
    asyncio.run(supportask.handler(core.db)(Event(type="server_started", ts=2000.0)))
    assert core.db.get_setting(supportask.STATE_KEY)["first_start"] == 1000.0


def test_an_older_install_counts_from_its_oldest_start_event(client):
    core = core_of(client)
    core.db.add_event("survival", "server_started", "Server started successfully")
    core.db.execute("UPDATE events SET ts = 500.0 WHERE type = 'server_started'")
    assert supportask.remember_start(core.db, ts=9000.0) == 500.0


def test_not_before_two_weeks(ready, client):
    started_days_ago(client, 13)
    assert ask(client) == {"show": False, "reason": "too_soon"}


def test_not_during_setup(ready, client):
    for ctx in ready.servers.values():
        ctx.db.set_setting(checklist.STATE_KEY, {})
    assert ask(client)["reason"] == "something_wrong"


def test_not_after_a_crash_or_another_problem(ready, client, monkeypatch):
    monkeypatch.setattr(recommendations, "evaluate", lambda facts: [a_tip("crash")])
    assert ask(client)["reason"] == "something_wrong"
    # A tip isn't a problem.
    monkeypatch.setattr(recommendations, "evaluate", lambda facts: [a_tip("mod_updates")])
    assert ask(client)["show"] is True


def test_not_after_a_failed_backup(ready, client, monkeypatch):
    ctx = next(iter(ready.servers.values()))
    monkeypatch.setattr(ctx.backups, "list_backups", lambda: [{"status": "failed"}])
    assert ask(client)["reason"] == "something_wrong"


def an_alert(core) -> int:
    return core.db.insert(
        "app_alerts",
        {
            "ts": time.time(),
            "server_id": "survival",
            "event": "server_crashed",
            "title": "Survival crashed",
            "body": "b",
            "page": "overview",
            "url": "u",
        },
    )


def test_not_while_an_alert_is_unread(ready, client):
    alert = an_alert(ready)
    assert ask(client)["reason"] == "something_wrong"
    assert client.get("/api/alerts?after=0").json()["read_up_to"] == 0
    # Read on any device: the marker is the account's, and only moves forward.
    response = client.post("/api/alerts/read", json={"up_to": alert + 50})
    assert response.json() == {"read_up_to": alert}  # never past the newest
    client.post("/api/alerts/read", json={"up_to": 0})
    assert client.get("/api/alerts?after=0").json()["read_up_to"] == alert
    assert ask(client)["show"] is True


def test_a_moderators_read_marker_is_their_own(ready, client):
    alert = an_alert(ready)
    sam = add_helper(client, dict(client.headers))
    assert client.post("/api/alerts/read", json={"up_to": alert}, headers=sam).status_code == 200
    assert ask(client)["reason"] == "something_wrong"


@pytest.mark.parametrize("choice", ["donate", "no_thanks"])
def test_gone_for_good_once_answered(ready, client, choice):
    response = client.post("/api/support-ask", json={"answer": choice})
    assert response.status_code == 200, response.text
    assert response.json() == {"show": False, "reason": "answered"}
    started_days_ago(client, 400)
    assert ask(client)["reason"] == "answered"
    assert client.post("/api/support-ask", json={"answer": "maybe"}).status_code == 422


def test_never_for_a_moderator(ready, client):
    owner = dict(client.headers)
    sam = add_helper(client, owner)
    client.post("/api/alerts/read", json={"up_to": 10**9})  # the "moderator added" alert
    assert client.get("/api/support-ask", headers=sam).status_code == 403
    assert (
        client.post("/api/support-ask", json={"answer": "no_thanks"}, headers=sam).status_code
        == 403
    )
    assert ask(client)["show"] is True
