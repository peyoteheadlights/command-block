"""The Discord bot's routes, read the way the phone apps read them
(mobile/android/core Community.kt, mobile/ios/Core Community.swift): each
key a phone model decodes is in the real answer, and what a phone sends
is taken. The bot's own behaviour is tested in tests/test_discord_*.py."""

from __future__ import annotations

from tests.fake_discord import GUILD_ID, wait_for
from tests.test_discord import (  # noqa: F401  (agent and fake are fixtures)
    agent,
    core_of,
    fake,
    run,
    set_up,
)
from tests.test_discord_levels import ALEX, linked


def keys(answer: dict, *wanted: str) -> None:
    missing = [key for key in wanted if key not in answer]
    assert not missing, f"the phones read {missing}, which the agent no longer sends"


def test_the_bot_page_has_what_the_phone_shows(agent, fake):  # noqa: F811
    set_up(agent)
    status = agent.get("/api/discord").json()
    keys(
        status,
        "token_set",
        "state",
        "detail",
        "switches",
        "features_off",
        "webhook_with_bot",
        "guilds",
    )
    guild = status["guilds"][0]
    keys(guild, "id", "name", "servers", "options", "report")
    # Remove on leave, the one Discord server switch the phone has.
    changed = agent.put(f"/api/discord/guilds/{GUILD_ID}/options", json={"remove_on_leave": True})
    assert changed.status_code == 200, changed.text
    assert changed.json()["guilds"][0]["options"]["remove_on_leave"] is True
    # It logs in again to hear who leaves.
    wait_for(lambda: agent.get("/api/discord").json()["state"] == "connected")
    checked = agent.post("/api/discord/check", json={})
    assert checked.status_code == 200, checked.text
    keys(checked.json()["guilds"][0]["report"], "problems")
    # The webhook-with-bot switch goes through PC settings.
    saved = agent.put("/api/settings", json={"updates": {"notifications.webhook_with_bot": True}})
    assert saved.status_code == 200, saved.text
    assert agent.get("/api/discord").json()["webhook_with_bot"] is True


def test_a_start_request_alert_is_one_the_phone_can_answer(agent, fake):  # noqa: F811
    set_up(agent)
    run(fake, "request-start")
    alerts = wait_for(
        lambda: [
            a
            for a in agent.get("/api/alerts", params={"after": 0}).json()["alerts"]
            if a["event"] == "start_request"
        ]
    )
    # Start / Say no show for this event, on this server, while it is young.
    assert alerts[0]["server_id"] == "test"
    assert agent.post("/api/servers/test/start-request", json={"answer": "no"}).json() == {
        "answer": "no"
    }
    # Answered elsewhere already: the phone shows the agent's words.
    gone = agent.post("/api/servers/test/start-request", json={"answer": "start"})
    assert gone.status_code == 404 and gone.json()["detail"].startswith(
        "There's no start request open"
    )
    # Read on the phone is read on the PC.
    read = agent.post("/api/alerts/read", json={"up_to": alerts[0]["id"]})
    assert read.json()["read_up_to"] >= alerts[0]["id"]


def test_linked_accounts_and_whitelist_help_have_what_the_phone_shows(agent, fake):  # noqa: F811
    set_up(agent)
    linked(core_of(agent), "Alex", ALEX)
    links = agent.get("/api/servers/test/discord-links").json()
    keys(links, "links", "requests", "players")
    keys(links["links"][0], "uuid", "name", "discord_id", "discord_name")
    removed = agent.delete(f"/api/servers/test/discord-links/{ALEX}")
    assert removed.status_code == 200 and removed.json()["links"] == []
    help = agent.get("/api/servers/test/whitelist").json()
    keys(help, "online_mode", "addon", "suggested", "ip_addon", "forms", "addresses")


def test_a_quest_made_on_the_phone_and_the_events_page(agent, fake):  # noqa: F811
    set_up(agent)
    # What Community.questBody sends: no end date, unused fields empty.
    made = agent.post(
        "/api/servers/test/quests",
        json={
            "title": "Play an hour",
            "details": "",
            "kind": "playtime",
            "goal": "",
            "count": 60,
            "repeat": "weekly",
            "ends": None,
            "rewards": [
                {"kind": "xp", "levels": 5},
                {"kind": "item", "item": "minecraft:cake", "count": 1},
            ],
            "suggestion_id": None,
        },
    )
    assert made.status_code == 200, made.text
    page = made.json()
    keys(page, "quests", "claims", "suggestions", "kinds", "bot", "guilds")
    quest = page["quests"][0]
    keys(
        quest,
        "id",
        "title",
        "details",
        "kind",
        "goal",
        "count",
        "repeat",
        "ends_at",
        "rewards",
        "joined",
        "members",
    )
    assert quest["ends_at"] is None
    ended = agent.delete(f"/api/servers/test/quests/{quest['id']}")
    assert ended.status_code == 200 and ended.json()["quests"] == []
    events = agent.get("/api/servers/test/quests/events").json()
    keys(events, "events", "polls", "kinds", "bot")
