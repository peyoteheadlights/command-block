"""The Discord bot across the owner's PCs (agent/discord/botpc.py): one Bot
PC with the live connection, Bot links to the others, commands and alerts
for servers on any PC, "That PC is off", and moving the bot.

Two real agents on HTTPS in one process (tests/test_shared_accounts.py's
PCs), both talking to the one fake Discord (tests/fake_discord.py).
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from agent.discord import client as discord_client
from agent.events import Event
from tests.fake_discord import GOOD_TOKEN, GUILD_ID, MEMBER_ID, FakeDiscord
from tests.test_shared_accounts import PC, join, wait_for
from tests.test_shared_accounts import pcs as pcs

ROLE = "2000000000000000002"  # "Friends of Sam" in the fake Discord


@pytest.fixture
def fake(monkeypatch):
    discord = FakeDiscord()
    monkeypatch.setattr(discord_client, "TRANSPORT", discord.transport())
    monkeypatch.setattr(discord_client, "CONNECT", discord.connect)
    # Pasting the token sets it in os.environ: setenv puts it back afterwards.
    monkeypatch.setenv("MCSC_DISCORD_BOT_TOKEN", "")
    return discord


def two_pcs(pcs) -> tuple[PC, PC, str]:
    """A and B in one account, the bot token pasted on A: A is the Bot PC."""
    a, b = pcs("a"), pcs("b")
    for pc in (a, b):
        pc.config.env_path = pc.config.source.parent / ".env"  # never the project's own
    owner = a.sign_in()
    join(b, a, owner)
    assert a.call("PUT", "/api/discord/token", owner, json={"token": GOOD_TOKEN}).status_code == 200
    wait_for(lambda: a.core.discord and a.core.discord.connected and a.core.discord.guilds)
    wait_for(lambda: b.core.discord and b.core.discord.rest_only and b.core.discord.ready)
    return a, b, owner


def remote(a: PC, b: PC) -> str:
    """B's server as A's bot names it, once B said it has it."""
    ref = f"test@{b.core.pcs.pc}"
    wait_for(lambda: ref in a.core.botpc.remote())
    return ref


def set_up(a: PC, b: PC, owner: str) -> str:
    ref = remote(a, b)
    made = a.call(
        "POST", f"/api/discord/guilds/{GUILD_ID}/setup", owner, json={"servers": ["test", ref]}
    )
    assert made.status_code == 200, made.text
    # B takes A's Discord settings: its own server is just "test" there.
    wait_for(lambda: b.core.db.query("SELECT 1 FROM discord_channels WHERE server_id = 'test'"))
    wait_for(lambda: GUILD_ID in b.core.discord.guilds)
    return ref


def grant(a: PC, owner: str, ref: str, *permissions: str, there: PC | None = None) -> None:
    """Give ROLE these on a server, on the Bot PC; ``there``: wait for the
    server's own PC to have it."""
    answer = a.call(
        "PUT",
        f"/api/discord/guilds/{GUILD_ID}/roles/{ROLE}",
        owner,
        json={"role_name": "Friends of Sam", "grants": {ref: list(permissions)}},
    )
    assert answer.status_code == 200, answer.text
    if there is not None:
        wait_for(
            lambda: there.core.db.query(
                "SELECT 1 FROM discord_role_grants WHERE role_id = ? AND server_id = 'test'",
                (ROLE,),
            )
        )


def on_pc(pc: PC, work):
    """Run a coroutine in that PC's agent (its own thread and loop)."""
    return asyncio.run_coroutine_threadsafe(work, pc.core.pcs._loop).result(10)


def run(fake, name, options=None, **kwargs) -> list[str]:
    token = fake.command(name, options, **kwargs)
    return wait_for(lambda: fake.replies(token), 15)


def link_headers(a: PC, b: PC) -> dict[str, str]:
    key = a.core.auth.records.keys[f"bot_link:{b.core.pcs.pc}"]
    return {
        "X-Command-Block-PC": a.core.pcs.pc,
        "X-Command-Block-Bot-Link": base64.b64encode(key).decode(),
    }


# ------------------------------------------------------------ the Bot PC and Bot links
def test_one_bot_pc_with_a_bot_link_to_the_other_pc(pcs, fake):
    a, b, owner = two_pcs(pcs)
    # Only A opened a live connection; B posts with the same token, no gateway.
    assert len(fake.sockets) == 1 and fake.identifies == 1
    status = a.call("GET", "/api/discord", owner).json()
    assert status["state"] == "connected" and status["bot_pc"]["this"]
    assert status["bot_pc"]["links"] == [
        {"pc": b.core.pcs.pc, "name": status["bot_pc"]["links"][0]["name"], "linked": True}
    ]
    on_b = b.call("GET", "/api/discord", owner).json()
    assert on_b["state"] == "elsewhere" and on_b["bot_pc"]["id"] == a.core.pcs.pc
    assert on_b["bot_pc"]["reach"]["state"] in ("ok", "sending")  # it answered
    # The token is in the record only encrypted, and never in an answer.
    row = b.core.db.query_one("SELECT body FROM sync_records WHERE kind = 'bot'")
    assert GOOD_TOKEN not in row["body"] and GOOD_TOKEN not in str(on_b)
    # B keeps only the link's hash; A keeps the key in its encrypted key file.
    link = b.core.auth.records.get("bot_link", b.core.pcs.pc)
    assert link["body"]["bot"] == a.core.pcs.pc and len(link["body"]["hash"]) == 64
    # Settings are A's to change.
    refused = b.call("PUT", "/api/discord/addresses", owner, json={"server_id": "test"})
    assert refused.status_code == 409 and "runs on" in refused.json()["detail"]


def test_a_bot_link_only_does_what_discord_can(pcs, fake):
    a, b, owner = two_pcs(pcs)
    headers = link_headers(a, b)
    with b.http() as http:
        # Outside the Discord list: refused on B, whatever the key.
        assert http.post("/api/sync/bot/console", json={}, headers=headers).status_code == 403
        unknown = {"interaction": {"type": 2, "data": {"name": "stop-everything"}}}
        answer = http.post("/api/sync/bot/interaction", json=unknown, headers=headers)
        assert answer.status_code == 403
        # A wrong key, or the key from another PC's header: refused.
        wrong = {**headers, "X-Command-Block-Bot-Link": base64.b64encode(b"x" * 32).decode()}
        assert http.post("/api/sync/bot/message", json={}, headers=wrong).status_code == 403
        posing = {**headers, "X-Command-Block-PC": b.core.pcs.pc}
        assert http.post("/api/sync/bot/message", json={}, headers=posing).status_code == 403
        # The Bot link is no sign-in: the API refuses it.
        assert http.get("/api/servers", headers=headers).status_code == 401
        key = headers["X-Command-Block-Bot-Link"]
        assert (
            http.get("/api/servers", headers={"Authorization": f"Bearer {key}"}).status_code == 401
        )
        assert http.post("/api/sync/bot/message", json={}, headers=headers).status_code == 200


# ------------------------------------------------------------ commands for B's servers
def test_status_and_start_work_for_a_server_on_the_other_pc(pcs, fake):
    a, b, owner = two_pcs(pcs)
    ref = set_up(a, b, owner)
    # Two servers called Test Server: the choices say which PC each is on.
    registered = wait_for(lambda: fake.made("PUT", "/applications/"))[-1]
    start = next(c for c in registered if c["name"] == "start")
    choices = next(o for o in start["options"] if o["name"] == "server")["choices"]
    assert {c["value"] for c in choices} == {f"test@{a.core.pcs.pc}", ref}
    assert all("(" in c["name"] for c in choices)
    # /status for B's server: B answers it.
    assert run(fake, "status", {"server": ref}) == ["Test Server is offline."]
    # /start needs the role's grant on B's server, given on A.
    words = run(fake, "start", {"server": ref}, roles=[ROLE])[-1]
    assert "don't allow" in words
    grant(a, owner, ref, "server.view", "server.control", there=b)
    words = run(fake, "start", {"server": ref}, roles=[ROLE])[-1]
    assert words.startswith("Starting Test Server")
    ctx = b.core.servers["test"]
    wait_for(lambda: ctx.server.state.value == "ONLINE", 30)
    assert a.core.servers["test"].server.state.value == "OFFLINE"
    # B wrote it in its own audit log, as the Discord member.
    assert b.core.db.query("SELECT 1 FROM audit_log WHERE action = 'discord_command'")
    assert b.call("POST", "/api/servers/test/server/stop", b.sign_in(), json={}).status_code == 200
    wait_for(lambda: ctx.server.state.value == "OFFLINE", 30)


# ------------------------------------------------------------ alerts from B
def test_alerts_from_the_other_pc_reach_discord_with_buttons_for_it(pcs, fake):
    a, b, owner = two_pcs(pcs)
    ref = set_up(a, b, owner)
    alerts = fake.channel_id("alerts")
    before = len(fake.in_channel("alerts"))
    on_pc(b, b.core.bus.publish(Event(type="backup_failed", message="disk full", server_id="test")))
    posted = wait_for(lambda: fake.in_channel("alerts")[before:], 15)
    buttons = [x["custom_id"] for row in posted[0]["components"] for x in row["components"]]
    assert buttons == [f"backup:{ref}:"]
    assert posted[0]["channel_id"] == alerts
    # Pressed on A: B answers it, and asks "Are you sure?" with its own buttons.
    token = fake.press(f"backup:{ref}:", roles=[ROLE])
    assert "Your Discord roles don't allow" in wait_for(lambda: fake.replies(token), 15)[-1]
    grant(a, owner, ref, "server.view", "backups.create", there=b)
    token = fake.press(f"backup:{ref}:", roles=[ROLE])
    asked = wait_for(lambda: fake.answers(token), 15)[-1]["data"]
    assert asked["content"] == "Back up Test Server now?"
    assert asked["components"][0]["components"][0]["custom_id"] == f"sure:backup:{ref}:"
    # The chat bridge: a line in B's game reaches B's chat channel.
    chat = [
        r["channel_id"]
        for r in b.core.db.query(
            "SELECT channel_id FROM discord_channels WHERE kind = 'chat' AND server_id = 'test'"
        )
    ]
    on_pc(
        b,
        b.core.bus.publish(
            Event(
                type="chat",
                message="<Alex> hi",
                server_id="test",
                data={"username": "Alex", "text": "hi", "kind": "player"},
            )
        ),
    )
    lines = wait_for(lambda: [m for m in fake.messages if m["channel_id"] in chat], 15)
    assert "hi" in lines[-1]["content"]


# ------------------------------------------------------------ That PC is off
def test_that_pc_is_off_and_start_tries_help_wake_first(pcs, fake, monkeypatch):
    from agent import wake

    a, b, owner = two_pcs(pcs)
    ref = set_up(a, b, owner)
    b.core.wake._save(on=True, mac="aa:bb:cc:dd:ee:ff", broadcast="192.168.1.255")
    b.core.botpc.publish()  # the bot's next minute, now
    wait_for(lambda: (a.core.botpc.news(b.core.pcs.pc).get("wake") or {}).get("key"))
    # B's own key for the bot is one its helping PC takes.
    key = a.core.botpc.news(b.core.pcs.pc)["wake"]["key"]
    assert wake.key_hash(key) in b.core.wake.key_hashes()
    sent = []
    monkeypatch.setattr(wake, "send_wake", lambda mac, broadcast: sent.append((mac, broadcast)))
    b.off()
    words = run(fake, "status", {"server": ref})[-1]
    assert words.startswith("That PC is off") and "Asked" not in words
    words = run(fake, "start", {"server": ref}, roles=[ROLE])[-1]
    assert "That PC is off" in words and "Asked" in words and "wake up" in words
    assert sent == [("aa:bb:cc:dd:ee:ff", "192.168.1.255")]


# ------------------------------------------------------------ moving the bot
def test_bot_offline_run_it_here_and_the_old_bot_pc_stops(pcs, fake):
    a, b, owner = two_pcs(pcs)
    set_up(a, b, owner)
    # Sam linked a Minecraft account on each PC's server.
    a.core.links.link("uuid-a", "Alex", "java", "test", MEMBER_ID, "sam", "test")
    b.core.links.link("uuid-b", "Steve", "java", "test", MEMBER_ID, "sam", "test")
    b.core.botpc.publish()
    wait_for(lambda: a.core.botpc.remote_links(MEMBER_ID))
    a.core.botpc.publish()
    wait_for(lambda: b.core.botpc.news(a.core.pcs.pc).get("links"))
    a.off()
    on_b = b.call("GET", "/api/discord", owner).json()
    assert on_b["state"] == "elsewhere" and on_b["bot_pc"]["reach"]["state"] == "unreachable"
    moved = b.call("POST", "/api/discord/bot-pc", owner)
    assert moved.status_code == 200, moved.text
    wait_for(lambda: b.core.discord and not b.core.discord.rest_only and b.core.discord.connected)
    assert fake.identifies == 2
    # B kept A's Discord settings, so nothing changes in Discord.
    roles = b.core.db.query("SELECT kind, role_id FROM discord_roles ORDER BY kind")
    assert roles and fake.made("PUT", "/applications/")
    # A comes back: it hears B is the Bot PC first, and never opens a second connection.
    a.on()
    wait_for(lambda: a.core.discord is not None and a.core.discord.rest_only)
    assert fake.identifies == 2
    assert a.core.db.query("SELECT kind, role_id FROM discord_roles ORDER BY kind") == roles
    # Commands for A's server now go from B to A over a new Bot link.
    ref = f"test@{a.core.pcs.pc}"
    wait_for(lambda: ref in b.core.botpc.remote())
    assert run(fake, "status", {"server": ref}) == ["Test Server is offline."]
    # Linked accounts on both PCs still count: /links from the new Bot PC.
    words = run(fake, "links")[-1]
    assert "Alex" in words and "Steve" in words


def test_a_removed_bot_link_or_pc_is_out_of_discord(pcs, fake):
    a, b, owner = two_pcs(pcs)
    ref = set_up(a, b, owner)
    removed = a.call("DELETE", f"/api/discord/bot-links/{b.core.pcs.pc}", owner)
    assert removed.status_code == 200
    assert removed.json()["bot_pc"]["links"][0]["linked"] is False
    assert run(fake, "status", {"server": ref}) == ["That server isn't there any more."]
    wait_for(lambda: b.core.auth.records.get("bot_link", b.core.pcs.pc) is None)
    added = a.call("PUT", f"/api/discord/bot-links/{b.core.pcs.pc}", owner)
    assert added.json()["bot_pc"]["links"][0]["linked"] is True
    wait_for(lambda: b.core.auth.records.get("bot_link", b.core.pcs.pc))
    assert run(fake, "status", {"server": ref}) == ["Test Server is offline."]
    # Removing B from Your PCs removes its Bot link too.
    assert a.call("DELETE", f"/api/pcs/{b.core.pcs.pc}", owner).status_code == 200
    assert a.core.auth.records.get("bot_link", b.core.pcs.pc) is None
    assert f"bot_link:{b.core.pcs.pc}" not in a.core.auth.records.keys
