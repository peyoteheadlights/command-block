"""The bot's part 3, against the fake Discord and the fake Minecraft server:
/players, /ip, the status message and presence line, alerts in the alerts
channel with their buttons, /start and start requests."""

from __future__ import annotations

import pytest

from agent import startrequests
from agent.discord import bot as discord_bot
from agent.discord.bot import BUTTONS
from agent.events import Event
from agent.security import permissions as P
from tests.fake_discord import GUILD_ID, MEMBER_ID, wait_for
from tests.test_discord import (  # noqa: F401  (agent and fake are fixtures)
    MOD_ROLE_OF_OWNER,
    agent,
    console,
    core_of,
    fake,
    online,
    run,
    set_up,
)

OTHER = "3000000000000000099"  # someone else in the Discord server


def grant(agent, *permissions):  # noqa: F811
    """Give the owner's own Discord role these permissions on the test server."""
    answer = agent.put(
        f"/api/discord/guilds/{GUILD_ID}/roles/{MOD_ROLE_OF_OWNER}",
        json={"role_name": "Friends of Sam", "grants": {"test": list(permissions)}},
    )
    assert answer.status_code == 200, answer.text


def refresh_live(agent):  # noqa: F811
    core = core_of(agent)
    agent.portal.call(core.discord.update_live)


def publish(agent, event: Event) -> None:  # noqa: F811
    agent.portal.call(core_of(agent).bus.publish, event)


def stop(agent):  # noqa: F811
    assert agent.post("/api/servers/test/server/stop", json={}).status_code == 200
    ctx = core_of(agent).servers["test"]
    wait_for(lambda: ctx.server.state.value == "OFFLINE", 20)


def buttons_of(message: dict) -> list[str]:
    return [b["custom_id"] for row in message.get("components") or [] for b in row["components"]]


# ------------------------------------------------------------ who may press what
def test_every_button_declares_a_permission_a_discord_role_could_be_given():
    for button in BUTTONS.values():
        assert button.permission in P.SERVER_PERMISSIONS, button.action
    # Kick, Ban, Restart and Backup ask "Are you sure?" first.
    assert {a for a, b in BUTTONS.items() if b.sure} == {"kick", "ban", "restart", "backup"}


# ------------------------------------------------------------ status and info
def test_players_says_offline_unknown_or_who_is_on(agent, fake):  # noqa: F811
    set_up(agent)
    assert run(fake, "players") == ["Test Server is offline, so nobody is playing."]
    ctx = online(agent)
    console(agent, "fakejoin Alex")
    wait_for(lambda: ctx.players.online())
    assert run(fake, "players") == ["Playing on Test Server (1/20): Alex"]
    ctx.players.verified_at = None
    assert "isn't known yet" in run(fake, "players")[0]
    stop(agent)


def test_ip_shows_the_shared_address_or_says_it_isnt_known(agent, fake):  # noqa: F811
    set_up(agent)
    words = run(fake, "ip")[-1]
    assert "From the internet: the owner hasn't added an address to share yet." in words
    assert (
        agent.put(
            "/api/discord/addresses", json={"server_id": "test", "address": "bad address!"}
        ).status_code
        == 422
    )
    saved = agent.put(
        "/api/discord/addresses", json={"server_id": "test", "address": "play.example.com"}
    )
    assert saved.json()["addresses"] == {"test": "play.example.com"}
    assert run(fake, "ip")[-1] == "Join Test Server (Java): `play.example.com`"
    agent.put("/api/discord/addresses", json={"server_id": "test", "address": None})
    assert agent.get("/api/discord").json()["addresses"] == {}


def test_the_status_message_edits_itself_and_the_presence_follows(agent, fake):  # noqa: F811
    set_up(agent)
    refresh_live(agent)
    posted = fake.in_channel("status")
    assert len(posted) == 1 and posted[0]["content"].startswith("**Test Server** is offline")
    presence = [m for m in fake.gateway_sent if m["op"] == 3]
    assert presence[-1]["d"]["activities"][0]["state"] == "Test Server is offline"
    # Nothing changed: nothing is sent.
    calls = len(fake.calls)
    refresh_live(agent)
    assert len(fake.calls) == calls
    ctx = online(agent)
    console(agent, "fakejoin Alex")
    wait_for(lambda: ctx.players.online())
    refresh_live(agent)
    posted = fake.in_channel("status")
    assert len(posted) == 1  # edited, not posted again
    assert "Players: 1/20 (Alex)" in posted[0]["content"]
    assert "Online since <t:" in posted[0]["content"]
    presence = [m for m in fake.gateway_sent if m["op"] == 3]
    assert presence[-1]["d"]["activities"][0]["state"] == "1 playing on Test Server"
    # Someone deletes the message: a new one is posted.
    fake.messages.remove(posted[0])
    ctx.players.verified_at = None
    refresh_live(agent)
    assert fake.in_channel("status")[0]["content"].count("Players: Unknown") == 1
    stop(agent)


# ------------------------------------------------------------ alerts
def test_alerts_go_to_the_alerts_channel_and_the_webhook_steps_aside(agent, fake):  # noqa: F811
    core = core_of(agent)
    sent_to_webhook = []

    async def webhook(event):
        sent_to_webhook.append(event.type)
        return True

    core.notifier.send_discord = webhook
    agent.put("/api/settings", json={"updates": {"notifications.discord_enabled": True}})
    # No alerts channel yet: the webhook still gets it.
    publish(agent, Event(type="backup_failed", message="disk full", server_id="test"))
    wait_for(lambda: sent_to_webhook == ["backup_failed"])
    set_up(agent)
    publish(agent, Event(type="backup_failed", message="disk full", server_id="test"))
    alert = wait_for(lambda: fake.in_channel("alerts"))[0]
    assert alert["embeds"][0]["title"] == "Test Server: backup failed"
    assert buttons_of(alert) == ["backup:test:"]
    assert sent_to_webhook == ["backup_failed"]
    # The owner wants both.
    agent.put("/api/settings", json={"updates": {"notifications.webhook_with_bot": True}})
    assert agent.get("/api/discord").json()["webhook_with_bot"] is True
    publish(agent, Event(type="backup_failed", message="disk full", server_id="test"))
    wait_for(lambda: len(sent_to_webhook) == 2 and len(fake.in_channel("alerts")) == 2)


def test_alerts_about_the_pc_never_reach_discord(agent, fake):  # noqa: F811
    set_up(agent)
    publish(agent, Event(type="auth_failure", level="error", message="from 1.2.3.4"))
    publish(agent, Event(type="backup_failed", message="later", server_id="test"))
    wait_for(lambda: fake.in_channel("alerts"))
    assert all("1.2.3.4" not in str(m) for m in fake.messages)


def test_a_server_can_have_its_own_alerts_channel(agent, fake):  # noqa: F811
    set_up(agent)
    general = "2000000000000000010"
    answer = agent.put(
        f"/api/discord/guilds/{GUILD_ID}/channels",
        json={"kind": "alerts", "server_id": "test", "channel_id": general},
    )
    assert answer.status_code == 200
    publish(agent, Event(type="backup_failed", server_id="test"))
    wait_for(lambda: fake.in_channel("general"))
    assert fake.in_channel("alerts") == []


def test_kick_asks_first_checks_the_role_and_waits_for_the_server(agent, fake):  # noqa: F811
    set_up(agent)
    ctx = online(agent)
    console(agent, "fakejoin Alex")
    joined = wait_for(lambda: fake.in_channel("alerts"))[-1]
    assert buttons_of(joined) == ["kick:test:Alex", "ban:test:Alex"]
    # Without a role that may: refused, nothing asked.
    token = fake.press("kick:test:Alex")
    assert wait_for(lambda: fake.replies(token)) == [
        "Your Discord roles don't allow that on this server."
    ]
    grant(agent, P.PLAYERS_MANAGE)
    token = fake.press("kick:test:Alex", roles=[MOD_ROLE_OF_OWNER])
    asked = wait_for(lambda: fake.answers(token))[0]
    assert asked["type"] == 4 and asked["data"]["flags"] == 64
    assert asked["data"]["content"] == "Kick Alex from Test Server?"
    assert buttons_of(asked["data"]) == ["sure:kick:test:Alex", "cancel"]
    assert ctx.players.online()  # nothing done yet
    token = fake.press("sure:kick:test:Alex", roles=[MOD_ROLE_OF_OWNER])
    done = wait_for(lambda: len(fake.answers(token)) > 1 and fake.answers(token))
    assert done[0] == {"type": 6}  # edits the question in place
    assert done[-1] == {"content": "Alex was kicked from Test Server.", "components": []}
    audit = core_of(agent).db.audit_entries()
    assert audit[0]["target"] == "button kick Alex" and audit[0]["user"] == "sam (Discord)"
    cancel = fake.press("cancel")
    assert wait_for(lambda: fake.answers(cancel))[0]["type"] == 7
    stop(agent)


# ------------------------------------------------------------ /start
def test_start_needs_start_and_has_a_cooldown(agent, fake, monkeypatch):  # noqa: F811
    set_up(agent)
    assert run(fake, "start") == ["Your Discord roles don't allow that on this server."]
    grant(agent, P.SERVER_CONTROL)
    token = fake.command("start", roles=[MOD_ROLE_OF_OWNER])
    words = wait_for(lambda: len(fake.answers(token)) > 1 and fake.replies(token))
    assert fake.answers(token)[0]["type"] == 5  # thinking first
    assert words[-1].startswith("Starting Test Server.")
    ctx = core_of(agent).servers["test"]
    wait_for(lambda: ctx.server.state.value == "ONLINE", 20)
    assert "is online already" in run(fake, "start", roles=[MOD_ROLE_OF_OWNER])[-1]
    stop(agent)
    assert "Try again in 5 min" in run(fake, "start", roles=[MOD_ROLE_OF_OWNER])[-1]
    audit = core_of(agent).db.audit_entries()
    assert audit[0]["target"] == "/start"


def test_a_start_that_fails_keeps_the_reason_on_the_pc(agent, fake):  # noqa: F811
    from agent.minecraft.process import ServerError

    set_up(agent)
    grant(agent, P.SERVER_CONTROL)
    ctx = core_of(agent).servers["test"]

    async def refuse(actor="system"):
        raise ServerError(r"Java wasn't found in C:\Users\mark\java")

    ctx.server.start = refuse
    words = run(fake, "start", roles=[MOD_ROLE_OF_OWNER])[-1]
    assert words == "Test Server didn't start. The owner can see why in Command Block."


# ------------------------------------------------------------ start requests
def test_a_start_request_reaches_the_alerts_channel_and_starting_tells_the_asker(
    agent,  # noqa: F811
    fake,  # noqa: F811
):
    set_up(agent)
    words = run(fake, "request-start")
    assert words[0].startswith("Asked.")
    assert "Someone already asked" in run(fake, "request-start", member=OTHER)[0]
    found = agent.get("/api/servers/test/start-request").json()["request"]
    assert found["who"] == "sam (Discord)" and found["expires_at"] - found["asked_at"] == 900
    alert = wait_for(lambda: fake.in_channel("alerts"))[-1]
    assert alert["embeds"][0]["title"] == "Test Server: start request"
    assert buttons_of(alert) == ["start_yes:test:", "start_no:test:"]
    # The asker can't answer their own request without Start.
    token = fake.press("start_yes:test:")
    assert "don't allow" in wait_for(lambda: fake.replies(token))[-1]
    grant(agent, P.SERVER_CONTROL)
    token = fake.press("start_yes:test:", roles=[MOD_ROLE_OF_OWNER], member=OTHER)
    assert wait_for(lambda: len(fake.answers(token)) > 1 and fake.replies(token)) == [
        "Starting Test Server."
    ]
    told = wait_for(
        lambda: [m for m in fake.messages if m.get("content", "").startswith(f"<@{MEMBER_ID}>")]
    )
    assert told[0]["content"] == f"<@{MEMBER_ID}> Test Server is starting. Give it a minute or two."
    # The alert loses its buttons and says how it ended.
    wait_for(lambda: fake.in_channel("alerts")[-1].get("components") == [])
    assert fake.in_channel("alerts")[-1]["content"] == "Answered: started."
    assert agent.get("/api/servers/test/start-request").json()["request"] is None
    ctx = core_of(agent).servers["test"]
    wait_for(lambda: ctx.server.state.value == "ONLINE", 20)
    assert "isn't stopped" in run(fake, "request-start", member=OTHER)[0]
    stop(agent)
    # 10 minutes between requests on a server.
    assert "Try again in 10 min" in run(fake, "request-start", member=OTHER)[0]


def test_saying_no_from_the_app_and_nobody_answering(agent, fake, monkeypatch):  # noqa: F811
    set_up(agent)
    run(fake, "request-start")
    answer = agent.post("/api/servers/test/start-request", json={"answer": "no"})
    assert answer.json() == {"answer": "no"}
    wait_for(lambda: any("said no" in m.get("content", "") for m in fake.messages))
    assert agent.post("/api/servers/test/start-request", json={"answer": "no"}).status_code == 404
    assert core_of(agent).servers["test"].server.state.value == "OFFLINE"
    # Nobody answers the next one in time.
    monkeypatch.setattr(startrequests, "LIFE", 0.2)
    core_of(agent).start_requests.last.clear()
    run(fake, "request-start")
    wait_for(lambda: any("Nobody answered" in m.get("content", "") for m in fake.messages))
    audit = core_of(agent).db.audit_entries(limit=50)
    assert any(a["action"] == "start_request" and a["detail"] == "no" for a in audit)


def test_starting_the_server_any_other_way_ends_the_request(agent, fake):  # noqa: F811
    set_up(agent)
    run(fake, "request-start")
    online(agent)
    wait_for(lambda: any("online now" in m.get("content", "") for m in fake.messages), 20)
    assert core_of(agent).start_requests.get("test") is None
    stop(agent)


def test_answering_takes_the_start_permission(agent):  # noqa: F811
    from agent.api.routes import api_routes

    route = next(
        r for path, r, _ in api_routes() if path.endswith("/start-request") and "POST" in r.methods
    )
    assert any(
        getattr(d.call, "permission", None) == P.SERVER_CONTROL
        for d in route.dependant.dependencies
    )


@pytest.fixture(autouse=True)
def _quick_live(monkeypatch):
    # The tests refresh the status message and presence themselves.
    monkeypatch.setattr(discord_bot, "LIVE_FIRST", 3600.0)
