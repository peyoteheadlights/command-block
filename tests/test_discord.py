"""The Discord bot, against the fake Discord (tests/fake_discord.py) and the
fake Minecraft server: connecting, the checks, "Set up this Discord server",
Discord role grants, the commands and linking. No test touches the network.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from agent.discord import client as discord_client
from agent.discord import layout
from agent.discord.bot import COMMANDS
from agent.discord.links import ALPHABET, LinkError, Links
from agent.security import permissions as P
from tests.conftest import PASSWORD, build_bedrock_config, build_config, build_multi_config
from tests.fake_discord import (
    GOOD_TOKEN,
    GUILD_ID,
    MEMBER_ID,
    FakeDiscord,
    wait_for,
)

MOD_ROLE_OF_OWNER = "2000000000000000002"  # "Friends of Sam", the owner's own role


@pytest.fixture
def fake(monkeypatch):
    discord = FakeDiscord()
    monkeypatch.setattr(discord_client, "TRANSPORT", discord.transport())
    monkeypatch.setattr(discord_client, "CONNECT", discord.connect)
    monkeypatch.setenv("MCSC_DISCORD_BOT_TOKEN", GOOD_TOKEN)
    return discord


def signed_in(config, monkeypatch):
    from agent.main import create_app
    from agent.security.auth import hash_password

    monkeypatch.setenv("MCSC_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MCSC_ADMIN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))
    monkeypatch.delenv("MCSC_API_TOKEN", raising=False)
    config.env_path = config.source.parent / ".env"  # never the project's own .env
    client = TestClient(create_app(config))
    client.__enter__()
    token = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).json()[
        "token"
    ]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.fixture
def agent(fake, tmp_path, monkeypatch):
    client = signed_in(build_config(tmp_path), monkeypatch)
    wait_for(lambda: client.get("/api/discord").json()["state"] == "connected")
    yield client
    client.__exit__(None, None, None)


@pytest.fixture
def multi_agent(fake, tmp_path, monkeypatch):
    client = signed_in(build_multi_config(tmp_path), monkeypatch)
    wait_for(lambda: client.get("/api/discord").json()["state"] == "connected")
    yield client
    client.__exit__(None, None, None)


def core_of(client):
    return client.app.state.core


def set_up(client, servers=("test",)):
    answer = client.post(f"/api/discord/guilds/{GUILD_ID}/setup", json={"servers": list(servers)})
    assert answer.status_code == 200, answer.text
    return answer.json()


def run(fake, name, options=None, **kwargs) -> list[str]:
    token = fake.command(name, options, **kwargs)
    return wait_for(lambda: fake.replies(token))


# ------------------------------------------------------------ connecting
def test_no_token_means_no_bot_and_no_discord_client(client, monkeypatch):
    monkeypatch.delenv("MCSC_DISCORD_BOT_TOKEN", raising=False)
    token = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).json()[
        "token"
    ]
    answer = client.get("/api/discord", headers={"Authorization": f"Bearer {token}"}).json()
    assert answer == {**answer, "token_set": False, "state": "off"}
    assert client.app.state.core.discord is None


def test_the_bot_connects_names_itself_and_registers_its_commands(agent, fake):
    status = wait_for(lambda: (s := agent.get("/api/discord").json())["guilds"] and s)
    assert status["state"] == "connected" and status["connected_at"]
    assert status["application_id"] and "permissions=" in status["invite_url"]
    assert status["switches"] == {"message_content": True, "members": True}
    # Named "Command Block" with the logo the first time.
    profile = wait_for(lambda: fake.made("PATCH", "/users/@me"))
    assert profile[0]["username"] == "Command Block"
    assert profile[0]["avatar"].startswith("data:image/png;base64,")
    registered = wait_for(lambda: fake.made("PUT", "/applications/"))
    # "quest join" and the others are registered as /quest's subcommands.
    assert {c["name"] for c in registered[-1]} == {n.split(" ")[0] for n in COMMANDS}
    assert status["guilds"][0]["name"] == "Friends"
    # The message intents only because the Message Content switch is on.
    identify = next(m for m in fake.gateway_sent if m["op"] == 2)
    assert identify["d"]["intents"] == (
        discord_client.GUILDS | discord_client.GUILD_MESSAGES | discord_client.MESSAGE_CONTENT
    )


def test_the_token_is_never_shown_logged_or_kept_in_the_config(agent, fake, caplog):
    caplog.set_level(logging.DEBUG)
    fake.socket.drop(1006)
    wait_for(lambda: fake.resumes == 1)
    for path in ("/api/discord", "/api/settings", "/api/export"):
        assert GOOD_TOKEN not in agent.get(path).text
    assert GOOD_TOKEN not in caplog.text
    from agent.config import SECRET_ENV_KEYS

    assert "MCSC_DISCORD_BOT_TOKEN" in SECRET_ENV_KEYS  # Get help removes it, Export encrypts it
    config_file = core_of(agent).config.source
    assert not config_file.exists() or GOOD_TOKEN not in config_file.read_text(encoding="utf-8")


def test_a_rejected_token_stops_the_bot_tells_the_owner_and_is_never_retried(
    fake, tmp_path, monkeypatch
):
    fake.token = "something else"
    client = signed_in(build_config(tmp_path), monkeypatch)
    try:
        wait_for(lambda: client.get("/api/discord").json()["state"] == "token_rejected")
        asked = len(fake.calls)
        import time

        time.sleep(0.5)
        # One call answered 401 (reading the switches), then nothing more.
        assert len(fake.calls) == asked == 1
        alerts = wait_for(lambda: client.get("/api/alerts?after=0").json()["alerts"])
        assert any("token" in a["title"].lower() for a in alerts)
        found = client.get("/api/servers/test/recommendations").json()
        assert "discord_problem" in {r["id"] for r in found["recommendations"]}
    finally:
        client.__exit__(None, None, None)


def test_a_dropped_connection_resumes_the_same_session(agent, fake):
    fake.socket.drop(1006)
    wait_for(lambda: fake.resumes == 1)
    assert fake.identifies == 1
    wait_for(lambda: agent.get("/api/discord").json()["state"] == "connected")


def test_a_session_discord_ended_logs_in_again(agent, fake):
    fake.socket.drop(4009)  # session timed out: can't be resumed
    wait_for(lambda: fake.identifies == 2)
    assert fake.resumes == 0


def test_a_silent_connection_is_noticed_by_the_heartbeat(fake, tmp_path, monkeypatch):
    fake.heartbeat_ms = 60
    fake.ack_heartbeats = False
    client = signed_in(build_config(tmp_path), monkeypatch)
    try:
        wait_for(lambda: fake.resumes >= 1)
    finally:
        client.__exit__(None, None, None)


def test_rest_waits_when_discord_asks_and_leaves_a_forbidden_channel_alone(fake):
    async def go():
        rest = discord_client.Rest(GOOD_TOKEN)
        fake.rate_limit_next = 0.05
        assert (await rest.request("GET", "/applications/@me"))["id"]
        fake.forbidden_channels.add("123")
        for _ in range(3):
            with pytest.raises(discord_client.DiscordError) as refused:
                await rest.request("POST", "/channels/123/messages", {"content": "hi"})
            assert refused.value.status == 403
        await rest.close()

    asyncio.run(go())
    assert len(fake.made("POST", "/channels/123")) == 1  # Discord was asked once
    assert len(fake.made("GET", "/applications/@me")) == 2  # the 429, then the answer


def test_wrong_tokens_are_refused_before_they_reach_env(agent):
    for bad in ("short", "has spaces in it " * 5, "a" * 60):
        assert agent.put("/api/discord/token", json={"token": bad}).status_code == 422


# ------------------------------------------------------------ setup and checks
def test_setup_creates_only_whats_missing_and_never_touches_the_owners_things(agent, fake):
    first = set_up(agent)
    assert set(first["made"]) >= {
        "Command Block",
        "alerts",
        "chat",
        "status",
        "quests",
        "levels",
        "events",
    }
    assert {"Minecraft mod", "Regular", "Veteran", "Legend"} <= set(first["made"])
    assert set_up(agent)["made"] == []
    # The owner deletes the chat channel: the next press makes it again.
    chat = next(c for c in fake.channels if c["name"] == "chat")
    fake.channels.remove(chat)
    assert set_up(agent)["made"] == ["chat"]
    # Nothing was ever renamed or deleted (a chat channel's slowmode is the
    # only change setup makes to a channel).
    assert not [c for c in fake.calls if c[0] in ("PATCH", "DELETE") and "/guilds/" in c[1]]
    assert all(
        c[2] == {"rate_limit_per_user": 5}
        for c in fake.calls
        if c[0] == "PATCH" and c[1].startswith("/channels/") and c[1].count("/") == 2
    )
    assert any(c["name"] == "general" for c in fake.channels)
    # The mod role got the default level on the server.
    status = agent.get("/api/discord").json()
    grants = status["guilds"][0]["grants"]
    assert [sorted(g["test"]) for g in grants.values()] == [sorted(P.LEVELS[P.DEFAULT_LEVEL])]
    assert status["guilds"][0]["servers"] == ["test"]
    assert status["guilds"][0]["report"]["problems"] == []


def test_chat_channels_keep_discords_slowmode_on(agent, fake):
    set_up(agent)
    chat = next(c for c in fake.channels if c["name"] == "chat")
    assert chat["rate_limit_per_user"] == layout.SLOWMODE == 5
    assert all("rate_limit_per_user" not in c for c in fake.channels if c["name"] != "chat")
    # Someone turns it off: the bot puts it back.
    chat["rate_limit_per_user"] = 0
    fake.socket.dispatch("CHANNEL_UPDATE", {**chat, "guild_id": GUILD_ID})
    wait_for(lambda: chat["rate_limit_per_user"] == 5)
    # A longer one is the owner's choice, and other channels aren't touched.
    chat["rate_limit_per_user"] = 30
    general = next(c for c in fake.channels if c["name"] == "general")
    calls = len(fake.calls)
    fake.socket.dispatch("CHANNEL_UPDATE", {**chat, "guild_id": GUILD_ID})
    fake.socket.dispatch("CHANNEL_UPDATE", {**general, "guild_id": GUILD_ID})
    assert run(fake, "status")  # the updates were handled before this answer
    assert not [c for c in fake.calls[calls:] if c[0] == "PATCH" and c[1].count("/") == 2]
    # Set up again on a chat channel without it: it's set.
    chat["rate_limit_per_user"] = 2
    set_up(agent)
    assert chat["rate_limit_per_user"] == 5


def test_several_servers_get_a_chat_and_status_channel_each(multi_agent, fake):
    set_up(multi_agent, ["survival", "creative"])
    names = {c["name"] for c in fake.channels}
    assert {"survival-chat", "survival-status", "creative-chat", "creative-status"} <= names
    assert "alerts" in names and "chat" not in names


def test_the_checks_name_exactly_whats_wrong(agent, fake):
    set_up(agent)
    core = core_of(agent)
    # The owner drags the Legend role above the bot's own role.
    legend = layout.our_roles(core.db, GUILD_ID)["level_25"]
    next(r for r in fake.roles if r["id"] == legend)["position"] = 9
    # ...takes away Manage Roles...
    next(r for r in fake.roles if r["id"] == fake.bot_roles[0])["permissions"] = str(1 << 4)
    # ...and hides the alerts channel from the bot.
    alerts = next(c for c in fake.channels if c["name"] == "alerts")
    alerts["permission_overwrites"] = [
        {"id": GUILD_ID, "type": 0, "allow": "0", "deny": str(1 << 10)}
    ]
    report = agent.post("/api/discord/check").json()["guilds"][0]["report"]
    found = {p["id"]: p for p in report["problems"]}
    assert found["role_too_low"]["role"] == "Legend"
    assert "manage_roles" in found
    assert found["channel_blocked"]["kind"] == "alerts"
    # An overwrite for the bot's own role lets it back in.
    alerts["permission_overwrites"].append(
        {"id": fake.bot_roles[0], "type": 0, "allow": str(1 << 10 | 1 << 11), "deny": "0"}
    )
    report = agent.post("/api/discord/check").json()["guilds"][0]["report"]
    assert "channel_blocked" not in {p["id"] for p in report["problems"]}


def test_missing_switches_only_turn_their_features_off(fake, tmp_path, monkeypatch):
    fake.flags = 0
    client = signed_in(build_config(tmp_path), monkeypatch)
    try:
        status = wait_for(
            lambda: (
                (s := client.get("/api/discord").json())["switches"].get("members") is False and s
            )
        )
        assert status["state"] == "connected"
        assert set(status["features_off"]) == {"chat_from_discord", "remove_on_leave"}
        # Asking for a switch that's off would stop the bot: only guild events.
        identify = next(m for m in fake.gateway_sent if m["op"] == 2)
        assert identify["d"]["intents"] == discord_client.GUILDS
    finally:
        client.__exit__(None, None, None)


def test_channel_permissions_follow_discords_order():
    roles = [
        {"id": "g", "permissions": str(layout.VIEW_CHANNEL)},
        {"id": "r", "permissions": "0"},
        {"id": "admin", "permissions": str(layout.ADMINISTRATOR)},
    ]
    channel = {
        "permission_overwrites": [
            {"id": "g", "type": 0, "allow": "0", "deny": str(layout.VIEW_CHANNEL)},
            {"id": "r", "type": 0, "allow": str(layout.POSTS), "deny": "0"},
            {"id": "me", "type": 1, "allow": "0", "deny": str(layout.SEND_MESSAGES)},
        ]
    }
    assert not layout.has(
        layout.channel_permissions("g", roles, "x", [], channel), layout.VIEW_CHANNEL
    )
    assert layout.has(layout.channel_permissions("g", roles, "x", ["r"], channel), layout.POSTS)
    assert not layout.has(
        layout.channel_permissions("g", roles, "me", ["r"], channel), layout.POSTS
    )
    assert layout.channel_permissions("g", roles, "me", ["admin"], channel) == -1


# ------------------------------------------------------------ who may do what
def test_every_command_declares_a_permission_a_discord_role_could_be_given():
    for command in COMMANDS.values():
        assert command.permission in P.SERVER_PERMISSIONS, command.name


def test_a_discord_member_never_gets_more_than_a_moderator_could(tmp_path):
    from agent.database.db import Database

    db = Database(tmp_path / "t.db")
    layout.tie(db, GUILD_ID, "chat", "survival", "1")
    layout.set_grant(db, GUILD_ID, "r1", "Mods", "survival", {P.PLAYERS_OP})
    with pytest.raises(ValueError):
        layout.set_grant(db, GUILD_ID, "r1", "Mods", "survival", {P.USERS_MANAGE})
    member = layout.member_principal(db, GUILD_ID, {"id": "1", "username": "sam"}, ["r1"])
    held = P.on_server(member, "survival")
    assert held == {P.SERVER_VIEW, P.PLAYERS_OP, P.PLAYERS_MANAGE}
    assert P.on_server(member, "creative") == frozenset()
    # None of a moderator's account extras, and nothing owner-only.
    for permission in (P.ACCOUNT, P.SETTINGS_VIEW, P.SYSTEM_VIEW, P.USERS_MANAGE, P.DISCORD_MANAGE):
        with pytest.raises(P.AuthError):
            P.check(member, permission, "survival")
    stranger = layout.member_principal(db, GUILD_ID, {"id": "2", "username": "bob"}, [])
    assert P.on_server(stranger, "survival") == {P.SERVER_VIEW}
    for command in COMMANDS.values():
        if command.permission != P.SERVER_VIEW:
            with pytest.raises(P.AuthError):
                P.check(stranger, command.permission, "survival")
    other_guild = layout.member_principal(db, "999", {"id": "1", "username": "sam"}, ["r1"])
    assert P.on_server(other_guild, "survival") == frozenset()


def test_role_grants_are_saved_through_the_api_and_refuse_owner_only_ones(agent, fake):
    set_up(agent)
    path = f"/api/discord/guilds/{GUILD_ID}/roles/{MOD_ROLE_OF_OWNER}"
    answer = agent.put(
        path, json={"role_name": "Friends of Sam", "grants": {"test": ["server.control"]}}
    )
    assert answer.status_code == 200
    grants = answer.json()["guilds"][0]["grants"][MOD_ROLE_OF_OWNER]
    assert grants == {"test": ["server.control", "server.view"]}
    refused = agent.put(path, json={"grants": {"test": ["users.manage"]}})
    assert refused.status_code == 400
    assert (
        agent.put(path, json={"grants": {"test": []}})
        .json()["guilds"][0]["grants"]
        .get(MOD_ROLE_OF_OWNER)
        is None
    )


def test_moderators_can_be_given_quests_and_events_but_never_discord_setup():
    assert P.QUESTS_MANAGE in P.SERVER_PERMISSIONS
    assert all(P.QUESTS_MANAGE not in level for level in P.LEVELS.values())
    assert P.DISCORD_MANAGE in P.ALL and P.DISCORD_MANAGE not in P.SERVER_PERMISSIONS


# ------------------------------------------------------------ commands
def test_status_answers_from_measured_facts_and_is_audited(agent, fake):
    set_up(agent)
    words = run(fake, "status")
    assert words == ["Test Server is offline."]
    audit = core_of(agent).db.audit_entries()
    assert audit[0]["action"] == "discord_command" and audit[0]["user"] == "sam (Discord)"
    assert audit[0]["target"] == "/status"


def test_status_says_unknown_rather_than_a_number_nobody_measured(agent, fake):
    set_up(agent)
    agent.post("/api/servers/test/server/start")
    ctx = core_of(agent).servers["test"]
    wait_for(lambda: ctx.server.state.value == "ONLINE", 20)
    ctx.players.verified_at = None
    words = run(fake, "status")[0]
    assert "Test Server is online." in words and "players: Unknown" in words
    agent.post("/api/servers/test/server/stop")


def test_a_discord_server_not_set_up_for_minecraft_sees_nothing(agent, fake):
    words = run(fake, "status")
    assert "isn't set up for a Minecraft server" in words[0]


def test_with_several_servers_the_channel_decides_or_the_bot_asks(multi_agent, fake):
    set_up(multi_agent, ["survival", "creative"])
    assert "Which server?" in run(fake, "status")[0]
    creative_chat = next(c["id"] for c in fake.channels if c["name"] == "creative-chat")
    assert run(fake, "status", channel=creative_chat) == ["Creative is offline."]
    assert run(fake, "status", {"server": "survival"}) == ["Survival is offline."]
    commands = fake.made("PUT", "/applications/")[-1]
    status = next(c for c in commands if c["name"] == "status")
    assert {c["value"] for c in status["options"][0]["choices"]} == {"survival", "creative"}


def test_members_are_rate_limited(agent, fake):
    set_up(agent)
    tokens = [fake.command("status") for _ in range(9)]
    last = wait_for(lambda: fake.replies(tokens[-1]))
    assert "Slow down" in last[0]
    # Someone else isn't held back by Sam.
    assert run(fake, "status", member="3000000000000000099") == ["Test Server is offline."]


# ------------------------------------------------------------ linking (Java)
def online(agent):
    agent.post("/api/servers/test/server/start")
    ctx = core_of(agent).servers["test"]
    wait_for(lambda: ctx.server.state.value == "ONLINE", 20)
    return ctx


def console(agent, words):
    answer = agent.post(
        "/api/servers/test/server/command", json={"command": words, "confirm": True}
    )
    assert answer.status_code == 200, answer.text


def link_code(fake, name="Alex", **kwargs) -> tuple[str, str]:
    token = fake.command("link", {"name": name}, **kwargs)
    words = wait_for(lambda: fake.replies(token))[0]
    return token, words.split(": ")[1].split()[0]


def test_a_code_typed_by_that_player_links_them(agent, fake):
    set_up(agent)
    ctx = online(agent)
    console(agent, "fakejoin Alex")
    token, code = link_code(fake)
    assert len(code) == 6 and set(code) <= set(ALPHABET)
    console(agent, f"fakechat Alex {code.lower()}")
    edits = wait_for(lambda: len(fake.replies(token)) > 1 and fake.replies(token))
    assert edits[-1] == "Linked: you play as Alex on Test Server."
    links = agent.get("/api/servers/test/discord-links").json()["links"]
    assert [(r["name"], r["discord_id"]) for r in links] == [("Alex", MEMBER_ID)]
    # The code line never reached the chat log, so nothing can bridge it.
    assert all(code.lower() not in m.text for m in ctx.chat.tail(50))
    # Used once: typing it again is ordinary chat.
    console(agent, f"fakechat Alex {code}")
    wait_for(lambda: any(m.text == code for m in ctx.chat.tail(50)))
    assert "Alex (linked on Test Server)" in run(fake, "links")[0]
    agent.post("/api/servers/test/server/stop")


def test_a_code_typed_by_someone_else_links_nobody(agent, fake):
    set_up(agent)
    online(agent)
    console(agent, "fakejoin Bob 11111111-2222-3333-4444-555555555555")
    token, code = link_code(fake)
    console(agent, f"fakechat Bob {code}")
    edits = wait_for(lambda: len(fake.replies(token)) > 1 and fake.replies(token))
    assert "Someone other than Alex typed your code" in edits[-1]
    assert agent.get("/api/servers/test/discord-links").json()["links"] == []
    agent.post("/api/servers/test/server/stop")


def test_an_expired_code_links_nobody(agent, fake):
    set_up(agent)
    online(agent)
    console(agent, "fakejoin Alex")
    token, code = link_code(fake)
    core_of(agent).links.codes[code].expires = 0
    console(agent, f"fakechat Alex {code}")
    edits = wait_for(lambda: len(fake.replies(token)) > 1 and fake.replies(token))
    assert "ran out" in edits[-1]
    assert agent.get("/api/servers/test/discord-links").json()["links"] == []
    agent.post("/api/servers/test/server/stop")


def test_wrong_names_are_refused():
    from agent.discord.bot import NAME_RE

    assert NAME_RE.match(".Sam") and NAME_RE.match("Alex_99")
    for bad in ("", "a b", "x" * 17, "<@123>", "Alex;stop"):
        assert not NAME_RE.match(bad)


def test_three_accounts_per_member_and_one_member_per_account(tmp_path):
    from agent.database.db import Database

    links = Links(Database(tmp_path / "t.db"))
    for i in range(3):
        links.link(f"uuid-{i}", f"Alt{i}", None, "s", "sam", "Sam", "test")
    with pytest.raises(LinkError, match="already have 3"):
        links.link("uuid-3", "Alt3", None, "s", "sam", "Sam", "test")
    with pytest.raises(LinkError, match="already have 3"):
        links.new_code("s", "Alt3", "sam", "Sam")
    with pytest.raises(LinkError, match="someone else"):
        links.link("uuid-0", "Alt0", None, "s", "bob", "Bob", "test")
    assert links.unlink("uuid-0") and len(links.of_member("sam")) == 2


def test_unlink_and_the_apps_remove_links(agent, fake):
    core = core_of(agent)
    set_up(agent)
    core.links.link("u-1", "Alex", None, "test", MEMBER_ID, "sam", "test")
    core.links.link("u-2", "Steve", None, "test", MEMBER_ID, "sam", "test")
    assert run(fake, "unlink", {"name": "alex"}) == ["Alex isn't linked to you any more."]
    assert "isn't one of your linked accounts" in run(fake, "unlink", {"name": "Herobrine"})[0]
    assert agent.delete("/api/servers/test/discord-links/u-2").status_code == 200
    assert core.links.of_member(MEMBER_ID) == []


# ------------------------------------------------------------ linking (Bedrock)
def test_bedrock_players_are_approved_in_the_app(fake, tmp_path, monkeypatch):
    client = signed_in(build_bedrock_config(tmp_path), monkeypatch)
    try:
        wait_for(lambda: client.get("/api/discord").json()["state"] == "connected")
        set_up(client, ["bedrock"])
        words = run(fake, "link", {"name": "Steve"})[0]
        assert "pick you from the server's players" in words
        core = core_of(client)
        core.db.execute(
            "INSERT INTO players (server_id, username, uuid, first_seen, last_seen) VALUES (?, ?, ?, 1, 1)",
            ("bedrock", "Big Steve", "2533274474361790"),
        )
        found = client.get("/api/servers/bedrock/discord-links").json()
        assert found["reads_chat"] is False
        request = found["requests"][0]
        assert request["name"] == "Steve" and found["players"][0]["uuid"] == "2533274474361790"
        bad = client.post(
            f"/api/servers/bedrock/discord-links/requests/{request['id']}",
            json={"answer": "approve", "uuid": "not-a-player"},
        )
        assert bad.status_code == 400
        done = client.post(
            f"/api/servers/bedrock/discord-links/requests/{request['id']}",
            json={"answer": "approve", "uuid": "2533274474361790"},
        ).json()
        assert done["requests"] == [] and done["links"][0]["name"] == "Big Steve"
        told = wait_for(
            lambda: [m for m in fake.messages if m.get("content", "").startswith(f"<@{MEMBER_ID}>")]
        )
        assert told[0]["content"].startswith(f"<@{MEMBER_ID}> you're linked")
        assert told[0]["allowed_mentions"] == {"users": [MEMBER_ID]}
        alerts = client.get("/api/alerts?after=0").json()["alerts"]
        assert any("link" in a["title"] for a in alerts)
    finally:
        client.__exit__(None, None, None)


def test_link_requests_need_the_players_permission(fake, multi_agent):
    from agent.api.routes import api_routes

    paths = {path: route for path, route, _scope in api_routes() if "discord-links" in path}
    assert paths and all(
        any(
            getattr(d.call, "permission", None) == P.PLAYERS_MANAGE
            for d in route.dependant.dependencies
        )
        for route in paths.values()
    )


def test_pasting_a_token_saves_it_in_env_and_connects(fake, tmp_path, monkeypatch):
    monkeypatch.delenv("MCSC_DISCORD_BOT_TOKEN")
    client = signed_in(build_config(tmp_path), monkeypatch)
    try:
        assert client.get("/api/discord").json()["state"] == "off"
        assert client.put("/api/discord/token", json={"token": GOOD_TOKEN}).status_code == 200
        wait_for(lambda: client.get("/api/discord").json()["state"] == "connected")
        env_path = core_of(client).config.env_path
        env = env_path.read_text(encoding="utf-8")
        assert f"MCSC_DISCORD_BOT_TOKEN={GOOD_TOKEN}" in env
        assert client.delete("/api/discord/token").json()["state"] == "off"
        assert GOOD_TOKEN not in env_path.read_text(encoding="utf-8")
        assert fake.socket.closed
    finally:
        client.__exit__(None, None, None)
