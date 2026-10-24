"""The bot's part 8, against the fake Discord and the fake Minecraft server:
/whitelist add and whitelist requests, whitelist add-ons on offline-mode
servers, taking people off when they leave the Discord server, the
addresses an IP whitelist add-on turned away, the console channel and
/report."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from agent import config as agent_config
from agent import transfer
from agent import whitelist as wl
from agent.discord import bridge, layout
from agent.events import Event
from agent.minecraft.console import ConsoleLine
from agent.security import permissions as P
from agent.whitelist import WhitelistError, mask
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
from tests.test_discord_levels import ALEX, OTHER, call, linked, played
from tests.test_discord_start import buttons_of, grant, publish, stop

ADDRESS = "203.0.113.7"


def last(fake, name, options=None, **kwargs) -> str:  # noqa: F811
    """A slash command's final answer (after "thinking" and "Sent")."""
    token = fake.command(name, options, **kwargs)
    wait_for(lambda: fake.replies(token) and "waiting" not in fake.replies(token)[-1])
    return fake.replies(token)[-1]


def pressed(fake, custom_id, **kwargs) -> str:  # noqa: F811
    token = fake.press(custom_id, **kwargs)
    return wait_for(lambda: fake.replies(token))[-1]


def whitelisted(ctx) -> list[str]:
    path = ctx.config.server_dir / "whitelist.json"
    return [p["name"] for p in json.loads(path.read_text())] if path.is_file() else []


def offline_mode(ctx) -> None:
    (ctx.config.server_dir / "server.properties").write_text(
        "server-port=25565\nonline-mode=false\n", encoding="utf-8"
    )


def has_addons(monkeypatch, *ids) -> None:
    """The server's mods folder holds these add-ons (read from the jars
    in real use; test_mods covers the reading)."""
    monkeypatch.setattr(wl, "installed", lambda ctx: set(ids))


def alerts_of(fake, kind: str) -> list[dict]:  # noqa: F811
    return [m for m in fake.in_channel("alerts") if any(kind in b for b in buttons_of(m))]


# ------------------------------------------------------------ /whitelist add
def test_whitelist_add_needs_players_and_waits_for_the_server(agent, fake):  # noqa: F811
    set_up(agent)
    assert run(fake, "whitelist add", {"name": "Bob"}) == [
        "Your Discord roles don't allow that on this server."
    ]
    grant(agent, P.PLAYERS_MANAGE)
    roles = [MOD_ROLE_OF_OWNER]
    words = last(fake, "whitelist add", {"name": "Bob"}, roles=roles)
    assert "has to be running" in words
    ctx = online(agent)
    token = fake.command("whitelist add", {"name": "Bob"}, roles=roles)
    words = wait_for(lambda: len(fake.replies(token)) > 1 and fake.replies(token))
    assert words[0] == "Sent to Test Server; waiting for the server to confirm it."
    wait_for(lambda: fake.replies(token)[-1] != words[0])
    assert fake.replies(token)[-1] == "Bob is on the whitelist of Test Server now."
    assert whitelisted(ctx) == ["Bob"]
    words = last(fake, "whitelist add", {"name": "Bob"}, roles=roles)
    assert words == "Bob was on the whitelist of Test Server already."
    assert "letters, numbers" in last(fake, "whitelist add", {"name": "Bob; op"}, roles=roles)
    audit = core_of(agent).db.audit_entries()
    assert {"/whitelist add Bob", "/whitelist add Bob; op"} <= {a["target"] for a in audit}
    assert audit[0]["user"] == "sam (Discord)"
    stop(agent)


def test_offline_mode_uses_the_whitelist_add_on_and_warns(agent, fake, monkeypatch):  # noqa: F811
    set_up(agent)
    grant(agent, P.PLAYERS_MANAGE)
    ctx = online(agent)
    has_addons(monkeypatch, "easywhitelist")
    # Online mode: Minecraft's own whitelist, whatever is installed.
    assert wl.whitelist_addon(ctx) is None
    offline_mode(ctx)
    assert wl.whitelist_addon(ctx).name == "EasyWhitelist"
    words = last(fake, "whitelist add", {"name": "Bob"}, roles=[MOD_ROLE_OF_OWNER])
    assert words.startswith("Bob is on the whitelist of Test Server now.")
    assert "offline mode" in words
    assert ctx.player_actions.recent()[0].command == "easywhitelist add Bob"
    page = agent.get("/api/servers/test/whitelist").json()
    assert (page["online_mode"], page["addon"]) == (False, "EasyWhitelist")
    stop(agent)


@pytest.mark.parametrize(
    "addon, sent",
    [("zerowhitelist", "zwl add Cat"), ("textwhitelist", "wltxt add player Cat")],
)
def test_each_whitelist_add_on_is_confirmed_by_its_own_line(
    agent,  # noqa: F811
    monkeypatch,
    addon,
    sent,
):
    ctx = online(agent)
    offline_mode(ctx)
    has_addons(monkeypatch, addon)
    pending = call(agent, ctx.player_actions.send, "whitelist_add", "Cat")
    assert pending.command == sent
    wait_for(lambda: pending.state == "done")
    pending = call(agent, ctx.player_actions.send, "whitelist_add", "Cat")
    wait_for(lambda: pending.state == "unchanged")
    pending = call(agent, ctx.player_actions.send, "whitelist_remove", "Cat")
    wait_for(lambda: pending.state == "done")
    stop(agent)


# ------------------------------------------------------------ whitelist requests
def test_a_request_is_approved_once_and_the_asker_hears(agent, fake):  # noqa: F811
    set_up(agent)
    assert run(fake, "whitelist ask", {"name": "Bob", "why": "  Sam's\nfriend  "})[0].startswith(
        "Sent."
    )
    form = agent.get("/api/servers/test/whitelist").json()["forms"][0]
    assert (form["name"], form["why"], form["discord_name"]) == ("Bob", "Sam's friend", "sam")
    alert = wait_for(lambda: alerts_of(fake, "form_yes"))[0]
    assert alert["embeds"][0]["title"] == "Test Server: someone asks to join"
    assert buttons_of(alert) == [f"form_yes:test:{form['id']}", f"form_no:test:{form['id']}"]
    # Asking again replaces the request: one per member.
    run(fake, "whitelist ask", {"name": "Bobby", "why": "typo"})
    forms = agent.get("/api/servers/test/whitelist").json()["forms"]
    assert [f["name"] for f in forms] == ["Bobby"]
    form_id = forms[0]["id"]
    # The asker can't approve their own request.
    assert "don't allow" in pressed(fake, f"form_yes:test:{form_id}")
    grant(agent, P.PLAYERS_MANAGE)
    roles = [MOD_ROLE_OF_OWNER]
    assert "Start Test Server first" in pressed(
        fake, f"form_yes:test:{form_id}", roles=roles, member=OTHER
    )
    ctx = online(agent)
    token = fake.press(f"form_yes:test:{form_id}", roles=roles, member=OTHER)
    wait_for(lambda: "now" in (fake.replies(token) or [""])[-1])
    assert whitelisted(ctx) == ["Bobby"]
    told = wait_for(
        lambda: [m for m in fake.messages if m.get("content", "").startswith(f"<@{MEMBER_ID}>")]
    )
    assert "Bobby is on the whitelist of Test Server now." in told[-1]["content"]
    assert "isn't there any more" in pressed(
        fake, f"form_yes:test:{form_id}", roles=roles, member=OTHER
    )
    stop(agent)


def test_two_quick_approvals_send_one_command(agent, fake):  # noqa: F811
    ctx = online(agent)
    core = core_of(agent)
    form = core.whitelist.ask(ctx, "Bob", "hi", MEMBER_ID, "sam", None)

    async def both():
        return await asyncio.gather(
            core.whitelist.answer(ctx, form["id"], True),
            core.whitelist.answer(ctx, form["id"], True),
            return_exceptions=True,
        )

    results = call(agent, both)
    assert sum(isinstance(r, WhitelistError) for r in results) == 1
    assert [p.command for p in ctx.player_actions.recent()] == ["whitelist add Bob"]
    stop(agent)


def test_turning_down_a_request_and_what_a_request_may_hold(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    ctx = core.servers["test"]
    run(fake, "whitelist ask", {"name": "Bob", "why": "hi"})
    form_id = agent.get("/api/servers/test/whitelist").json()["forms"][0]["id"]
    refused = agent.post(f"/api/servers/test/whitelist/forms/{form_id}", json={"answer": "refuse"})
    assert refused.status_code == 200, refused.text
    assert refused.json()["forms"] == []
    told = wait_for(
        lambda: [m for m in fake.messages if m.get("content", "").startswith(f"<@{MEMBER_ID}>")]
    )
    assert "turned down" in told[-1]["content"]
    with pytest.raises(WhitelistError, match="why"):
        core.whitelist.ask(ctx, "Bob", "   ", "1", "x", None)
    with pytest.raises(WhitelistError):
        core.whitelist.ask(ctx, "../Bob", "hi", "1", "x", None)
    for member in range(wl.MAX_FORMS):
        core.whitelist.ask(ctx, "Bob", "hi", str(member), "x", None)
    with pytest.raises(WhitelistError, match="requests waiting"):
        core.whitelist.ask(ctx, "Bob", "hi", "new member", "x", None)
    # Old requests go after 7 days.
    core.db.execute("UPDATE whitelist_forms SET created_at = 0")
    assert core.whitelist.forms("test") == []
    assert (
        agent.post("/api/servers/test/whitelist/forms/999", json={"answer": "approve"}).status_code
        == 400
    )


# ------------------------------------------------------------ leaving the Discord server
def test_leaving_the_discord_server_takes_them_off_only_when_asked(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    ctx = online(agent)
    console(agent, "whitelist add Alex")
    wait_for(lambda: whitelisted(ctx) == ["Alex"])
    linked(core, "Alex", ALEX)
    # Off by default: leaving changes nothing.
    fake.socket.dispatch("GUILD_MEMBER_REMOVE", {"guild_id": GUILD_ID, "user": {"id": MEMBER_ID}})
    identifies = fake.identifies
    turned_on = agent.put(f"/api/discord/guilds/{GUILD_ID}/options", json={"remove_on_leave": True})
    assert turned_on.status_code == 200, turned_on.text
    assert turned_on.json()["guilds"][0]["options"] == {"remove_on_leave": True}
    # The bot logs in again to ask Discord who leaves.
    wait_for(lambda: fake.identifies > identifies)
    wait_for(lambda: agent.get("/api/discord").json()["state"] == "connected")
    sent = [m for m in fake.gateway_sent if m["op"] == 2][-1]
    assert sent["d"]["intents"] & (1 << 1)
    assert whitelisted(ctx) == ["Alex"]
    stop(agent)
    fake.socket.dispatch("GUILD_MEMBER_REMOVE", {"guild_id": GUILD_ID, "user": {"id": MEMBER_ID}})
    wait_for(lambda: core.db.get_setting(wl.REMOVALS_KEY, {}).get("test") == ["Alex"])
    online(agent)
    wait_for(lambda: whitelisted(ctx) == [])
    assert core.db.get_setting(wl.REMOVALS_KEY, {}) == {}
    stop(agent)


# ------------------------------------------------------------ new addresses
def refused(agent, name="Alex", address=ADDRESS, why="IP mismatch"):  # noqa: F811
    console(agent, f"fakeline [IPWL SECURITY] Blocked {name} from {address}: {why}")


def test_a_known_player_turned_away_is_asked_about_masked_and_encrypted(
    agent,  # noqa: F811
    fake,  # noqa: F811
    monkeypatch,
):
    monkeypatch.delenv(wl.KEY_ENV, raising=False)
    set_up(agent)
    core = core_of(agent)
    ctx = online(agent)
    played(core, "Alex", ALEX, 2)
    # Without an IP whitelist add-on nothing is kept.
    refused(agent)
    console(agent, "list")
    has_addons(monkeypatch, "ipwl")
    refused(agent, name="Zed")  # a stranger: nothing kept
    refused(agent)
    row = wait_for(lambda: core.db.query_one("SELECT * FROM ip_addresses"))
    assert core.db.query("SELECT name FROM ip_addresses") == [{"name": "Alex"}]
    assert ADDRESS not in json.dumps(row) and row["masked"] == "203.0.x.x"
    env = (ctx.config.env_path or core.config.env_path).read_text()
    assert wl.KEY_ENV in env
    alert = wait_for(lambda: alerts_of(fake, "address_allow"))[0]
    assert alert["embeds"][0]["title"] == "Test Server: a player tried to join from a new address"
    assert buttons_of(alert) == [
        f"address_allow:test:{row['id']}",
        f"address_ignore:test:{row['id']}",
    ]
    assert ADDRESS not in json.dumps(fake.messages)
    page = agent.get("/api/servers/test/whitelist").json()
    assert page["ip_addon"] == "IPWL" and ADDRESS not in json.dumps(page)
    assert page["addresses"][0]["masked"] == "203.0.x.x"
    # Turned away again from the same address: no second alert.
    refused(agent)
    console(agent, "list")
    assert len(alerts_of(fake, "address_allow")) == 1
    # Allow sends the add-on's command once.
    grant(agent, P.PLAYERS_MANAGE)
    roles = [MOD_ROLE_OF_OWNER]
    words = pressed(fake, f"address_allow:test:{row['id']}", roles=roles)
    wait_for(lambda: "Allowed" in words or "Sent" in words)
    assert ctx.player_actions.recent()[0].command == f"ipwl addip Alex {ADDRESS}"
    assert "allowed already" in pressed(fake, f"address_allow:test:{row['id']}", roles=roles)
    # Taken off the whitelist: the address goes, from the add-on's list too.
    console(agent, "whitelist add Alex")
    wait_for(lambda: whitelisted(ctx) == ["Alex"])
    pending = call(agent, ctx.player_actions.send, "whitelist_remove", "Alex")
    wait_for(lambda: pending.state == "done")
    wait_for(lambda: core.db.query("SELECT * FROM ip_addresses") == [])
    wait_for(
        lambda: any(
            p.command == f"ipwl removeip Alex {ADDRESS}" for p in ctx.player_actions.recent()
        )
    )
    stop(agent)


def test_ignored_and_removed_addresses_and_a_gone_add_on(agent, fake, monkeypatch):  # noqa: F811
    monkeypatch.setenv(wl.KEY_ENV, "A" * 43 + "=")
    set_up(agent)
    core = core_of(agent)
    ctx = online(agent)
    played(core, "Alex", ALEX, 2)
    has_addons(monkeypatch, "ipwl")
    refused(agent, why="Player not in whitelist")
    row = wait_for(lambda: core.db.query_one("SELECT * FROM ip_addresses"))
    assert row["why"] == "new"
    ignored = agent.post(
        f"/api/servers/test/whitelist/addresses/{row['id']}", json={"answer": "ignore"}
    )
    assert ignored.json()["addresses"][0]["state"] == "ignored"
    refused(agent, why="Player not in whitelist")
    console(agent, "list")
    assert len(alerts_of(fake, "address_allow")) == 1
    allowed = agent.post(
        f"/api/servers/test/whitelist/addresses/{row['id']}", json={"answer": "allow"}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["action"]["command"] == "ipwl add Alex 203.0.x.x"
    assert ctx.player_actions.recent()[0].command == f"ipwl add Alex {ADDRESS}"
    wait_for(lambda: ctx.player_actions.recent()[0].state == "done")
    assert (
        agent.delete(f"/api/servers/test/whitelist/addresses/{row['id']}").json()["addresses"] == []
    )
    wait_for(lambda: ctx.player_actions.recent()[0].command == f"ipwl removeip Alex {ADDRESS}")
    refused(agent, address="198.51.100.4")
    wait_for(lambda: core.db.query_one("SELECT * FROM ip_addresses"))
    # The add-on is gone: so are the addresses.
    has_addons(monkeypatch)
    assert agent.get("/api/servers/test/whitelist").json()["addresses"] == []
    assert core.db.query("SELECT * FROM ip_addresses") == []
    stop(agent)


def test_minecrafts_own_refusal_counts_only_when_the_name_was_allowed(
    agent,  # noqa: F811
    fake,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setenv(wl.KEY_ENV, "A" * 43 + "=")
    core = core_of(agent)
    ctx = online(agent)
    played(core, "Alex", ALEX, 2)
    has_addons(monkeypatch, "ipwhitelist")
    (ctx.config.server_dir / "server.properties").write_text("white-list=true\n")
    line = ConsoleLine(
        seq=1,
        ts=0,
        raw="",
        message="Disconnecting Alex (/[2001:db8::1]:51234): \u00a7cYou are not white-listed on "
        "this server!",
    )
    assert call(agent, core.whitelist.heard, ctx, line) is None  # the name whitelist refused
    console(agent, "whitelist add Alex")
    wait_for(lambda: whitelisted(ctx) == ["Alex"])
    assert call(agent, core.whitelist.heard, ctx, replace(line, source="command")) is None
    assert call(agent, core.whitelist.heard, ctx, line)
    row = wait_for(lambda: core.db.query_one("SELECT * FROM ip_addresses"))
    assert row["masked"] == "2001:db8:x"
    pending = call(agent, core.whitelist.allow, ctx, row["id"], "admin")
    assert pending.command == "ipwhitelist addip 2001:db8::1"
    wait_for(lambda: pending.state == "done")
    stop(agent)


def test_addresses_are_masked_and_kept_out_of_get_help():
    assert (mask("203.0.113.7"), mask("2001:0db8::1"), mask("fe80::1%3")) == (
        "203.0.x.x",
        "2001:db8:x",
        "fe80:0:x",
    )
    assert wl.KEY_ENV in agent_config.SECRET_ENV_KEYS  # redacted from Get help's logs
    assert {"ip_addresses", "whitelist_forms"} <= set(transfer.DATA_TABLES)  # Export everything


# ------------------------------------------------------------ the console channel
def test_console_lines_hide_addresses_and_link_codes():
    assert (
        bridge.console_line(
            "[12:00:01] [Server thread/INFO]: Alex[/203.0.113.7:51234] logged in with entity id 1"
        )
        == "[12:00:01] [Server thread/INFO]: Alex[/[address hidden]:51234] logged in with entity id 1"
    )
    assert "[address hidden]" in bridge.console_line("Disconnecting Bo (/[2001:db8::1]:5): bye")
    kept = "[12:00:01] [Server thread/INFO]: Starting minecraft server version 1.21.4"
    assert bridge.console_line(kept) == kept
    assert bridge.console_line("<Alex> K7PQ2M") is None
    assert "`" not in bridge.console_line("say ```hi```")
    assert (
        bridge.console_line(r"Loading C:\Users\mark\server")
        == r"Loading C:\Users\..."[:22] + r"\server"
    )
    posts = bridge.console_posts(["a"] * 3, left_out=7)
    assert posts[0].startswith("```") and "7 lines left out" in posts[0]


def test_the_console_channel_is_off_by_default_and_read_only(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    assert layout.consoles(core.db, {GUILD_ID}) == {}
    assert "console" not in {c["name"] for c in fake.channels}
    general = "2000000000000000010"
    no_server = agent.put(
        f"/api/discord/guilds/{GUILD_ID}/channels", json={"kind": "console", "channel_id": general}
    )
    assert no_server.status_code == 400
    tied = agent.put(
        f"/api/discord/guilds/{GUILD_ID}/channels",
        json={"kind": "console", "server_id": "test", "channel_id": general},
    )
    assert tied.status_code == 200, tied.text
    # @everyone can see #general: the checks say so.
    problems = tied.json()["guilds"][0]["report"]["problems"]
    assert {"id": "console_public", "channel": "general", "server": "Test Server"} in problems
    found = agent.get("/api/servers/test/recommendations").json()["recommendations"]
    assert any(r["title"] == "Everyone can see a console channel" for r in found)
    ctx = online(agent)
    console(agent, f"fakeline Alex[/{ADDRESS}:51234] logged in")
    posted = wait_for(
        lambda: [m for m in fake.in_channel("general") if "logged in" in m["content"]]
    )
    assert ADDRESS not in json.dumps(fake.messages)
    assert posted[0]["content"].startswith("```") and posted[0]["allowed_mentions"] == {"parse": []}
    # Typing in it sends nothing to the server.
    sent = len(ctx.player_actions.recent())
    fake.message("stop", general)
    console(agent, "list")
    assert ctx.server.state.value == "ONLINE" and len(ctx.player_actions.recent()) == sent
    stop(agent)


def test_a_console_burst_is_capped(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    general = "2000000000000000010"
    agent.put(
        f"/api/discord/guilds/{GUILD_ID}/channels",
        json={"kind": "console", "server_id": "test", "channel_id": general},
    )
    for i in range(500):
        publish(agent, Event(type="console", server_id="test", message=f"line {i}"))
    posted = wait_for(lambda: fake.in_channel("general"))
    text = "".join(m["content"] for m in posted)
    assert "line 499" in text and "line 0\n" not in text and "left out" in text
    assert len(posted) <= 3
    assert core.discord._console_out == {}


# ------------------------------------------------------------ /report
def test_a_report_opens_a_private_thread_with_the_chat(agent, fake):  # noqa: F811
    set_up(agent)
    online(agent)
    console(agent, "fakechat Alex hello")
    console(agent, "fakechat Bob *grief*")
    wait_for(lambda: len(core_of(agent).servers["test"].chat) == 2)
    words = run(fake, "report", {"player": "Bob", "reason": "broke my house"})[-1]
    thread = next(c for c in fake.channels if c.get("type") == 12)
    assert thread["parent_id"] == fake.channel_id("alerts") and thread["invitable"] is False
    assert words.endswith(f"<#{thread['id']}>")
    assert fake.thread_members[thread["id"]] == {MEMBER_ID}
    first = next(m for m in fake.messages if m["channel_id"] == thread["id"])
    assert "Reason: broke my house" in first["content"] and "\\*grief\\*" in first["content"]
    assert first["allowed_mentions"] == {"parse": []}
    alert = wait_for(lambda: alerts_of(fake, "report_see"))[0]
    assert alert["embeds"][0]["title"] == "Test Server: a player was reported"
    report_id = alert["components"][0]["components"][0]["custom_id"].split(":")[2]
    assert "don't allow" in pressed(fake, f"report_see:test:{report_id}", member=OTHER)
    grant(agent, P.PLAYERS_MANAGE)
    words = pressed(fake, f"report_see:test:{report_id}", member=OTHER, roles=[MOD_ROLE_OF_OWNER])
    assert words == f"Added you to the report: <#{thread['id']}>"
    assert fake.thread_members[thread["id"]] == {MEMBER_ID, OTHER}
    stop(agent)


def test_reports_are_limited_checked_and_need_an_alerts_channel(agent, fake):  # noqa: F811
    set_up(agent)
    for _ in range(3):
        assert run(fake, "report", {"player": "Bob", "reason": "x"})[-1].startswith("Thanks.")
    assert "3 reports in the last hour" in run(fake, "report", {"player": "Bob", "reason": "x"})[-1]
    assert (
        "letters, numbers"
        in run(fake, "report", {"player": "a b", "reason": "x"}, member=OTHER)[-1]
    )
    first = next(m for m in fake.messages if m["content"].startswith(f"<@{MEMBER_ID}> reported"))
    assert "doesn't show its chat" not in first["content"]
    assert "Nobody said anything" in first["content"]
    core_of(agent).db.execute("DELETE FROM discord_channels WHERE kind = 'alerts'")
    words = run(fake, "report", {"player": "Bob", "reason": "x"}, member=OTHER)[-1]
    assert "no alerts channel" in words
