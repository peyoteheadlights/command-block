"""The bot's part 6, against the fake Discord and the fake Minecraft server:
quests (making, joining, progress from real data, repeats), rewards only from
templates, "done" claims with Approve / Refuse, ideas from /quest suggest,
and the self-editing board."""

from __future__ import annotations

import datetime
import json

import pytest

from agent import levels, quests
from agent.addons import world_path
from agent.discord import layout
from agent.discord.bot import COMMANDS, command_list
from agent.quests import QuestError, check_quest, check_rewards, period_of
from agent.security import permissions as P
from tests.conftest import build_bedrock_config
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
    signed_in,
)
from tests.test_discord_levels import ALEX, OTHER, call, linked, played, write_stats
from tests.test_helpers import add_helper

SLAYER = "2000000000000000060"  # a plain Discord role, made by the owner
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def slayer(fake):  # noqa: F811
    fake.roles.append(
        {
            "id": SLAYER,
            "name": "Dragon Slayer",
            "position": 1,
            "color": 0xAA0000,
            "permissions": "0",
        }
    )


def make(agent, **wanted):  # noqa: F811
    body = {"title": "Diamonds", "kind": "playtime", "count": 1, **wanted}
    answer = agent.post("/api/servers/test/quests", json=body)
    assert answer.status_code == 200, answer.text
    return answer.json()["quests"][-1]


def check(agent):  # noqa: F811
    core = core_of(agent)
    call(agent, core.discord.update_quests)


def lines(ctx):
    return [line.raw for line in ctx.server.console.tail(2000)]


def rewards(core, state=None):
    rows = core.db.query("SELECT * FROM quest_rewards ORDER BY id")
    return [r for r in rows if state in (None, r["state"])]


# ------------------------------------------------------------ what a quest may hold
def test_rewards_only_come_from_the_templates():
    assert check_rewards(
        [
            {"kind": "item", "item": "diamond", "count": 64},
            {"kind": "xp", "levels": 30},
            {"kind": "level_xp", "amount": 500},
            {"kind": "role", "role_id": SLAYER},
        ]
    ) == [
        {"kind": "item", "item": "minecraft:diamond", "count": 64},
        {"kind": "xp", "levels": 30},
        {"kind": "level_xp", "amount": 500},
        {"kind": "role", "role_id": SLAYER},
    ]
    for wrong in (
        [{"kind": "command", "command": "op Alex"}],
        [{"kind": "item", "item": "diamond 64\nop Alex", "count": 1}],
        [{"kind": "item", "item": "diamond; op Alex", "count": 1}],
        [{"kind": "item", "item": "diamond", "count": 65}],
        [{"kind": "xp", "levels": 31}],
        [{"kind": "level_xp", "amount": 501}],
        [{"kind": "role", "role_id": "@everyone"}],
        [{"kind": "xp", "levels": 1}, {"kind": "xp", "levels": 2}],
        ["give Alex diamond"],
    ):
        with pytest.raises(QuestError):
            check_rewards(wrong)


def test_quest_values_are_checked(agent, fake):  # noqa: F811
    ctx = core_of(agent).servers["test"]
    made = check_quest(
        ctx, {"title": "  Miner\n", "kind": "stat", "goal": "minecraft.mined:minecraft.stone"}
    )
    assert (made["title"], made["goal"]) == ("Miner", "minecraft:mined minecraft:stone")
    assert check_quest(ctx, {"title": "M", "kind": "stat", "goal": "mined:stone"})["goal"] == (
        "minecraft:mined minecraft:stone"
    )
    for wrong, words in (
        ({"title": "", "kind": "joins"}, "title"),
        ({"title": "x" * 81, "kind": "joins"}, "80 characters"),
        ({"title": "A", "kind": "teleport"}, "Pick what"),
        ({"title": "A", "kind": "stat", "goal": "../../x"}, "stat"),
        (
            {"title": "A", "kind": "advancement", "goal": "end/kill_dragon", "repeat": "daily"},
            "once",
        ),
        ({"title": "A", "kind": "joins", "count": 0}, "from 1"),
        ({"title": "A", "kind": "items", "goal": "diamond", "count": 9999}, "from 1"),
        ({"title": "A", "kind": "joins", "ends": "2001-01-01"}, "gone"),
        ({"title": "A", "kind": "joins", "repeat": "hourly"}, "how often"),
    ):
        with pytest.raises(QuestError, match=words):
            check_quest(ctx, wrong)


def test_bedrock_quests_are_what_the_console_shows(fake, tmp_path, monkeypatch):  # noqa: F811
    client = signed_in(build_bedrock_config(tmp_path), monkeypatch)
    try:
        sid = next(iter(core_of(client).servers))
        page = client.get(f"/api/servers/{sid}/quests").json()
        assert page["kinds"] == ["playtime", "joins", "approval"]
        answer = client.post(
            f"/api/servers/{sid}/quests", json={"title": "A", "kind": "stat", "goal": "mined:stone"}
        )
        assert answer.status_code == 400 and "doesn't keep stats files" in answer.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_periods_reset_at_midnight_and_weeks_on_monday():
    sunday = datetime.datetime(2026, 10, 25, 23, 59)
    assert period_of("daily", sunday) == "2026-10-25"
    assert period_of("weekly", sunday) == "2026-10-19"  # that week's Monday
    assert period_of("weekly", sunday + datetime.timedelta(minutes=1)) == "2026-10-26"
    assert period_of("monthly", sunday) == "2026-10"
    assert period_of("yearly", sunday) == "2026"
    assert period_of("once", sunday) == "once"


# ------------------------------------------------------------ joining and progress
def test_a_stat_quest_counts_from_joining_and_gives_its_rewards(agent, fake):  # noqa: F811
    set_up(agent)
    slayer(fake)
    core = core_of(agent)
    world = world_path(core.servers["test"])
    played(core, "Alex", ALEX, 1)
    write_stats(world, stats={"stats": {"minecraft:mined": {"minecraft:diamond_ore": 40}}})
    quest = make(
        agent,
        title="Diamond digger",
        kind="stat",
        goal="mined:diamond_ore",
        count=10,
        rewards=[
            {"kind": "item", "item": "diamond", "count": 5},
            {"kind": "xp", "levels": 3},
            {"kind": "level_xp", "amount": 100},
            {"kind": "role", "role_id": SLAYER},
        ],
    )
    assert "Link your Minecraft account" in run(fake, "quest join", {"quest": quest["id"]})[0]
    linked(core, "Alex", ALEX)
    assert "There's no quest 99" in run(fake, "quest join", {"quest": 99})[0]
    words = run(fake, "quest join", {"quest": quest["id"]})[0]
    assert words.startswith(f"You're on quest {quest['id']}, Diamond digger")
    assert "already" in run(fake, "quest join", {"quest": quest["id"]})[0]
    # The 40 mined before joining don't count.
    check(agent)
    assert "0/10" in run(fake, "quest progress")[0]
    write_stats(
        world,
        stats={"stats": {"minecraft:mined": {"minecraft:diamond_ore": 46}}},
        saved_at=1_700_000_000,
    )
    check(agent)
    words = run(fake, "quest progress")[0]
    assert "6/10" in words and "as last saved <t:1700000000:R>" in words
    write_stats(world, stats={"stats": {"minecraft:mined": {"minecraft:diamond_ore": 55}}})
    ctx = online(agent)
    console(agent, "fakejoin Alex")
    wait_for(lambda: ctx.players.online())
    check(agent)
    assert "done!" in run(fake, "quest progress")[0]
    # Level XP at once, the items and Minecraft XP while Alex is online, the
    # Discord role from the bot: each once, each confirmed.
    assert levels.all_xp(core.db)[MEMBER_ID] >= 100
    wait_for(lambda: not rewards(core, "waiting") and not rewards(core, "sending"))
    assert {r["kind"]: r["state"] for r in rewards(core)} == {
        "item": "done",
        "xp": "done",
        "level_xp": "done",
        "role": "done",
    }
    sent = [line for line in lines(ctx) if "give " in line or "xp add" in line]
    assert len(sent) == 2
    assert fake.members[MEMBER_ID] == [SLAYER]
    check(agent)  # nothing given twice
    assert len(rewards(core)) == 4
    board = fake.in_channel("quests")
    assert len(board) == 1 and "**1. Diamond digger**" in board[0]["content"]
    assert f"Done: <@{MEMBER_ID}>" in board[0]["content"]
    assert board[0]["allowed_mentions"] == {"parse": []}


def test_an_offline_player_gets_items_on_their_next_join(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    played(core, "Alex", ALEX, 0)
    linked(core, "Alex", ALEX)
    quest = make(
        agent,
        kind="approval",
        title="Build a house",
        rewards=[{"kind": "item", "item": "cake", "count": 1}],
    )
    run(fake, "quest join", {"quest": quest["id"]})
    run(fake, "quest done", {"quest": quest["id"]})
    claim = agent.get("/api/servers/test/quests").json()["claims"][0]
    answer = agent.post(
        f"/api/servers/test/quests/claims/{claim['id']}", json={"answer": "approve"}
    )
    assert answer.status_code == 200
    assert [r["state"] for r in answer.json()["rewards"]] == ["waiting"]
    ctx = online(agent)
    console(agent, "fakejoin Alex")
    wait_for(lambda: rewards(core, "done"))
    assert any("give Alex minecraft:cake 1" in line for line in lines(ctx))


def test_a_reward_with_no_answer_stays_sent_and_is_never_sent_again(agent, fake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(quests, "WAIT", 0)  # the console's answer comes too late
    set_up(agent)
    core = core_of(agent)
    played(core, "Alex", ALEX, 0)
    linked(core, "Alex", ALEX)
    quest = make(agent, kind="approval", rewards=[{"kind": "item", "item": "cake", "count": 1}])
    ctx = online(agent)
    console(agent, "fakejoin Alex")
    wait_for(lambda: ctx.players.online())
    run(fake, "quest done", {"quest": quest["id"]})
    claim = agent.get("/api/servers/test/quests").json()["claims"][0]
    agent.post(f"/api/servers/test/quests/claims/{claim['id']}", json={"answer": "approve"})
    wait_for(lambda: rewards(core, "sent"))
    console(agent, "fakejoin Alex")
    check(agent)
    assert [r["state"] for r in rewards(core)] == ["sent"]
    assert sum("give Alex minecraft:cake 1" in line for line in lines(ctx)) == 1


def test_an_item_the_server_doesnt_know_is_failed_not_retried(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    played(core, "Alex", ALEX, 0)
    linked(core, "Alex", ALEX)
    quest = make(agent, kind="approval", rewards=[{"kind": "item", "item": "nothing", "count": 1}])
    ctx = online(agent)
    console(agent, "fakejoin Alex")
    wait_for(lambda: ctx.players.online())
    run(fake, "quest done", {"quest": quest["id"]})
    claim = agent.get("/api/servers/test/quests").json()["claims"][0]
    agent.post(f"/api/servers/test/quests/claims/{claim['id']}", json={"answer": "approve"})
    failed = wait_for(lambda: rewards(core, "failed"))
    assert "Unknown item" in failed[0]["answer"]


def test_finishing_again_stacks_the_discord_role(agent, fake, monkeypatch):  # noqa: F811
    set_up(agent)
    slayer(fake)
    core = core_of(agent)
    played(core, "Alex", ALEX, 0)
    linked(core, "Alex", ALEX)
    quest = make(
        agent, kind="joins", count=1, repeat="daily", rewards=[{"kind": "role", "role_id": SLAYER}]
    )
    run(fake, "quest join", {"quest": quest["id"]})
    for times, day in ((1, "2026-10-24"), (2, "2026-10-25"), (3, "2026-10-26")):
        monkeypatch.setattr(quests, "period_of", lambda repeat, now=None, d=day: d)
        check(agent)  # the new day's start
        core.db.execute("UPDATE players SET sessions = sessions + 1 WHERE uuid = ?", (ALEX,))
        check(agent)
        assert core.quests.times_finished(quest["id"], MEMBER_ID) == times
    made = {r["name"]: r for r in fake.roles}
    assert made["Dragon Slayer ×2"]["color"] == 0xAA0000
    assert made["Dragon Slayer ×3"]["permissions"] == "0"
    # Only the highest stack is held.
    assert fake.members[MEMBER_ID] == [made["Dragon Slayer ×3"]["id"]]
    # Unfinished rows of past days are gone; finished ones stay to count.
    rows = core.db.query("SELECT period FROM quest_progress ORDER BY period")
    assert [r["period"] for r in rows] == ["2026-10-24", "2026-10-25", "2026-10-26"]


def test_a_reward_role_with_powers_is_refused_and_checked_again(agent, fake):  # noqa: F811
    set_up(agent)
    fake.roles.append(
        {"id": "2000000000000000061", "name": "Kicker", "position": 1, "permissions": str(1 << 1)}
    )
    answer = agent.post(
        "/api/servers/test/quests",
        json={
            "title": "A",
            "kind": "joins",
            "rewards": [{"kind": "role", "role_id": "2000000000000000061"}],
        },
    )
    assert answer.status_code == 400 and "gives power in Discord" in answer.json()["detail"]
    answer = agent.post(
        "/api/servers/test/quests",
        json={"title": "A", "kind": "joins", "rewards": [{"kind": "role", "role_id": GUILD_ID}]},
    )
    assert answer.status_code == 400 and "can't be a quest reward" in answer.json()["detail"]
    # Allowed when made, given powers in Discord later: never handed out.
    slayer(fake)
    core = core_of(agent)
    played(core, "Alex", ALEX, 0)
    linked(core, "Alex", ALEX)
    quest = make(agent, kind="joins", rewards=[{"kind": "role", "role_id": SLAYER}])
    run(fake, "quest join", {"quest": quest["id"]})
    fake.roles[-1]["permissions"] = str(1 << 3)  # Administrator
    core.db.execute("UPDATE players SET sessions = sessions + 1")
    check(agent)
    assert rewards(core, "failed") and fake.members[MEMBER_ID] == []


# ------------------------------------------------------------ bring items
def test_items_are_counted_never_taken(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    played(core, "Alex", ALEX, 0)
    linked(core, "Alex", ALEX)
    quest = make(agent, kind="items", goal="diamond", count=10, title="Bring diamonds")
    words = run(fake, "quest done", {"quest": quest["id"]})[-1]
    assert "Join Test Server as one of your linked accounts" in words
    ctx = online(agent)
    console(agent, "fakejoin Alex")
    wait_for(lambda: ctx.players.online())
    console(agent, "fakeitem Alex minecraft:diamond 4")
    assert run(fake, "quest done", {"quest": quest["id"]})[-1].startswith("You have 4 of the 10")
    console(agent, "fakeitem Alex minecraft:diamond 12")
    assert "done! Your items stay with you" in run(fake, "quest done", {"quest": quest["id"]})[-1]
    checks = [line for line in lines(ctx) if "clear " in line]
    assert checks and all(line.endswith("clear Alex minecraft:diamond 0") for line in checks)
    assert "finished quest" in run(fake, "quest done", {"quest": quest["id"]})[-1]


# ------------------------------------------------------------ approval
def test_a_claim_with_a_screenshot_is_approved_once(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    played(core, "Alex", ALEX, 0)
    linked(core, "Alex", ALEX)
    quest = make(
        agent, kind="approval", title="Build a castle", rewards=[{"kind": "level_xp", "amount": 50}]
    )
    fake.files["/attachments/1/2/castle.png"] = PNG
    picture = {
        "attachments": {
            "77": {"url": "https://cdn.discordapp.com/attachments/1/2/castle.png", "size": len(PNG)}
        }
    }
    words = run(
        fake,
        "quest done",
        {"quest": quest["id"], "note": "by the river", "screenshot": "77"},
        resolved=picture,
    )
    assert words[-1].startswith("Sent.")
    # The bot's token never goes to the image server.
    assert all("authorization" not in h for h in fake.cdn_headers)
    assert "already waiting" in run(fake, "quest done", {"quest": quest["id"]})[-1]
    page = agent.get("/api/servers/test/quests").json()
    claim = page["claims"][0]
    assert (claim["title"], claim["note"], claim["discord_name"]) == (
        "Build a castle",
        "by the river",
        "sam",
    )
    shot = agent.get(f"/api/servers/test/quests/claims/{claim['id']}/screenshot").json()
    assert shot["type"] == "png"
    # The alert in the alerts channel has Approve / Refuse.
    alert = wait_for(lambda: [m for m in fake.in_channel("alerts") if m.get("components")])
    buttons = [b["custom_id"] for b in alert[-1]["components"][0]["components"]]
    assert buttons == [f"claim_yes:test:{claim['id']}", f"claim_no:test:{claim['id']}"]
    # Pressed by someone without "Quests and events": refused.
    token = fake.press(buttons[0])
    assert "don't allow" in wait_for(lambda: fake.replies(token))[-1]
    answer = agent.post(
        f"/api/servers/test/quests/claims/{claim['id']}", json={"answer": "approve"}
    )
    assert answer.status_code == 200 and answer.json()["claims"] == []
    # The second answer (the app and Discord at once) finds nothing.
    again = agent.post(f"/api/servers/test/quests/claims/{claim['id']}", json={"answer": "refuse"})
    assert again.status_code == 400
    assert levels.all_xp(core.db)[MEMBER_ID] == 50
    assert not list((core.config.data_dir / quests.SCREENSHOTS).iterdir())  # deleted once answered
    told = wait_for(lambda: [m for m in fake.messages if "approved" in m.get("content", "")])
    assert told[0]["allowed_mentions"] == {"users": [MEMBER_ID]}


def test_only_real_pictures_from_discord_are_kept(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    played(core, "Alex", ALEX, 0)
    linked(core, "Alex", ALEX)
    quest = make(agent, kind="approval")
    fake.files["/attachments/1/2/x.png"] = b"MZ not a picture"
    for url, size, words in (
        ("https://evil.example/x.png", 10, "didn't come from Discord"),
        ("http://cdn.discordapp.com/attachments/1/2/x.png", 10, "didn't come from Discord"),
        ("https://cdn.discordapp.com/attachments/1/2/x.png", 9 * 1024 * 1024, "too big"),
        ("https://cdn.discordapp.com/attachments/1/2/x.png", 10, "PNG or JPEG"),
    ):
        picture = {"attachments": {"5": {"url": url, "size": size}}}
        said = run(fake, "quest done", {"quest": quest["id"], "screenshot": "5"}, resolved=picture)
        assert words in said[-1]
    assert core.quests.claims("test") == []


def test_claims_of_an_ended_quest_go_with_their_screenshots(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    played(core, "Alex", ALEX, 0)
    linked(core, "Alex", ALEX)
    quest = make(agent, kind="approval")
    fake.files["/a/b.png"] = PNG
    picture = {"attachments": {"1": {"url": "https://cdn.discordapp.com/a/b.png", "size": 9}}}
    run(fake, "quest done", {"quest": quest["id"], "screenshot": "1"}, resolved=picture)
    page = agent.delete(f"/api/servers/test/quests/{quest['id']}").json()
    assert page["quests"] == [] and page["claims"] == []
    assert not list((core.config.data_dir / quests.SCREENSHOTS).iterdir())
    assert "no quest" in run(fake, "quest join", {"quest": quest["id"]})[0]


# ------------------------------------------------------------ ideas and making from Discord
def test_ideas_are_answered_in_the_app(agent, fake):  # noqa: F811
    set_up(agent)
    for i in range(3):
        assert run(fake, "quest suggest", {"title": f"Idea {i}", "details": "fun"})[0].startswith(
            "Thanks"
        )
    assert "3 ideas waiting" in run(fake, "quest suggest", {"title": "Idea 4"})[0]
    ideas = agent.get("/api/servers/test/quests").json()["suggestions"]
    assert [s["title"] for s in ideas] == ["Idea 0", "Idea 1", "Idea 2"]
    answer = agent.post(
        "/api/servers/test/quests",
        json={"title": "Idea 0", "kind": "joins", "count": 3, "suggestion_id": ideas[0]["id"]},
    )
    assert [s["title"] for s in answer.json()["suggestions"]] == ["Idea 1", "Idea 2"]
    agent.delete(f"/api/servers/test/quests/suggestions/{ideas[1]['id']}")
    told = wait_for(lambda: len([m for m in fake.messages if "idea" in m.get("content", "")]) == 2)
    assert told
    assert agent.delete(f"/api/servers/test/quests/suggestions/{ideas[1]['id']}").status_code == 404


def test_quest_create_needs_quests_and_events(agent, fake):  # noqa: F811
    set_up(agent)
    slayer(fake)
    options = {
        "title": "Slay",
        "kind": "advancement",
        "goal": "end/kill_dragon",
        "role": SLAYER,
        "level_xp": 99,
    }
    assert "don't allow" in run(fake, "quest create", options)[-1]
    agent.put(
        f"/api/discord/guilds/{GUILD_ID}/roles/{MOD_ROLE_OF_OWNER}",
        json={"role_name": "Friends of Sam", "grants": {"test": ["quests.manage"]}},
    )
    words = run(fake, "quest create", options, roles=[MOD_ROLE_OF_OWNER])[-1]
    assert words.startswith("Made **1. Slay**: earn the advancement end/kill dragon")
    quest = agent.get("/api/servers/test/quests").json()["quests"][0]
    assert quest["rewards"] == [
        {"kind": "role", "role_id": SLAYER},
        {"kind": "level_xp", "amount": 99},
    ]
    assert quest["created_by"] == "sam (Discord)"
    bad = run(fake, "quest create", {**options, "repeat": "daily"}, roles=[MOD_ROLE_OF_OWNER])[-1]
    assert "earned once" in bad


def test_quests_routes_need_quests_and_events(agent, fake):  # noqa: F811
    owner = dict(agent.headers)
    sam = add_helper(agent, owner, servers=["test"])
    assert agent.get("/api/servers/test/quests", headers=sam).status_code == 403
    agent.put("/api/accounts/sam", json={"grants": {"test": ["quests.manage"]}}, headers=owner)
    assert agent.get("/api/servers/test/quests", headers=sam).status_code == 200


def test_the_quest_commands_are_one_group_with_subcommands():
    registered = command_list({"test": "Test Server"})
    group = next(c for c in registered if c["name"] == "quest")
    assert group["type"] == 1
    assert {o["name"] for o in group["options"]} == {
        "join",
        "progress",
        "done",
        "suggest",
        "create",
    }
    assert all(o["type"] == 1 for o in group["options"])
    assert COMMANDS["quest create"].permission == P.QUESTS_MANAGE
    assert COMMANDS["quest join"].permission == P.SERVER_VIEW
    # Discord allows 25 options a command.
    assert all(len(o["options"]) <= 25 for o in group["options"])


def test_the_board_lists_quests_and_edits_one_message(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    check(agent)
    first = fake.in_channel("quests")
    assert len(first) == 1 and "No quests right now" in first[0]["content"]
    make(agent, title="Joiner", kind="joins", count=2, repeat="weekly")
    check(agent)
    board = fake.in_channel("quests")
    assert len(board) == 1  # edited, not posted again
    assert (
        "**1. Joiner**: join 2 times · every week · reward: bragging rights" in board[0]["content"]
    )
    calls = len(fake.calls)
    check(agent)
    assert not [c for c in fake.calls[calls:] if c[0] == "PATCH"]  # nothing changed: no edit
    saved = core.db.get_setting("discord_quest_boards")
    assert list(saved.values())[0]["message"] == board[0]["id"]
    assert json.dumps(saved)  # kept as plain data


def test_quest_tables_go_in_export_everything():
    from agent.transfer import DATA_TABLES

    assert {"quests", "quest_progress", "quest_suggestions", "quest_rewards"} <= set(DATA_TABLES)
    assert layout.level_role_refused("g", {"id": "g", "name": "@everyone"}, {}) == (
        "@everyone can't be a Level role."
    )
    assert OTHER  # a member id other tests share
