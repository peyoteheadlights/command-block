"""The bot's part 5, against the fake Discord: the stats reader and /stats,
Levels (XP, /level, Level roles, level-ups) and the weekly leaderboard."""

from __future__ import annotations

import datetime
import json
import os
import time

from agent import levels
from agent.addons import world_path
from agent.discord import layout
from agent.stats import read_files
from tests.conftest import build_bedrock_config
from tests.fake_discord import GUILD_ID, MEMBER_ID, wait_for
from tests.test_discord import (  # noqa: F401  (agent and fake are fixtures)
    agent,
    core_of,
    fake,
    run,
    set_up,
    signed_in,
)
from tests.test_helpers import add_helper

ALEX = "069a79f4-44e9-4726-a5be-fca90e38aaf5"
ALT = "11111111-2222-3333-4444-555555555555"
OTHER = "3000000000000000099"


def write_stats(world, uuid=ALEX, stats=None, advancements=None, saved_at=None):
    for folder, data in (("stats", stats), ("advancements", advancements)):
        if data is None:
            continue
        path = world / folder / f"{uuid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        if saved_at:
            os.utime(path, (saved_at, saved_at))


def played(core, name, uuid, hours, server_id="test"):
    core.db.execute(
        "INSERT INTO players (server_id, username, uuid, first_seen, last_seen, total_seconds, "
        "sessions) VALUES (?, ?, ?, 1, 1, ?, 3)",
        (server_id, name, uuid, hours * 3600),
    )


def linked(core, name, uuid, member=MEMBER_ID):
    core.links.link(uuid, name, "java", "test", member, "sam", "test")


def call(agent, work, *args):  # noqa: F811
    return agent.portal.call(work, *args)


def level_role(core, level):
    return dict(layout.level_roles(core.db, GUILD_ID))[level]


# ------------------------------------------------------------ the stats reader
def test_only_stats_in_the_files_are_read_with_their_saved_time(tmp_path):
    write_stats(
        tmp_path,
        stats={
            "stats": {
                "minecraft:custom": {
                    "minecraft:play_time": 72000 * 3,  # ticks: 3 hours
                    "minecraft:deaths": 4,
                    "create:wrench_used": 12,
                    "create:goggles": 3,
                },
                "minecraft:mined": {"minecraft:stone": 100, "create:zinc_ore": 5},
            }
        },
        advancements={
            "minecraft:story/mine_stone": {"done": True, "criteria": {}},
            "minecraft:story/smelt_iron": {"done": False, "criteria": {}},
            "minecraft:recipes/misc/torch": {"done": True, "criteria": {}},
            "DataVersion": 3953,
        },
        saved_at=1_700_000_000,
    )
    found = read_files(tmp_path, ALEX)
    # No mob_kills in the file: no "mobs", rather than a made-up 0.
    assert found["values"] == {"playtime": 3 * 3600, "deaths": 4, "blocks": 105, "advancements": 1}
    assert found["other"] == [
        {"name": "create:wrench_used", "value": 12},
        {"name": "create:goggles", "value": 3},
    ]
    assert found["saved_at"] == 1_700_000_000


def test_older_worlds_broken_files_and_odd_ids_give_nothing_made_up(tmp_path):
    write_stats(
        tmp_path, stats={"stats": {"minecraft:custom": {"minecraft:play_one_minute": 1200}}}
    )
    assert read_files(tmp_path, ALEX)["values"] == {"playtime": 60}
    assert read_files(tmp_path, ALT) == {"values": {}, "other": [], "saved_at": None}
    (tmp_path / "stats" / f"{ALEX}.json").write_text("{not json", encoding="utf-8")
    assert read_files(tmp_path, ALEX)["saved_at"] is None
    # An id that isn't a uuid never becomes a path.
    assert read_files(tmp_path, "../../secret")["saved_at"] is None


# ------------------------------------------------------------ /stats
def test_stats_say_when_they_were_saved_and_use_your_own_links(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    ctx = core.servers["test"]
    played(core, "Alex", ALEX, 2)
    words = run(fake, "stats", {"player": "alex"})[0]
    assert words.startswith("Alex has no stats saved on Test Server yet")
    write_stats(
        world_path(ctx),
        stats={"stats": {"minecraft:custom": {"minecraft:deaths": 2, "minecraft:mob_kills": 9}}},
        saved_at=1_700_000_000,
    )
    words = run(fake, "stats", {"player": "Alex"})[0]
    assert words.splitlines() == [
        "**Alex** on Test Server, last saved <t:1700000000:R>",
        "Deaths: 2",
        "Mobs killed: 9",
    ]
    assert run(fake, "stats")[0] == "Add a player name, or link your account with /link first."
    linked(core, "Alex", ALEX)
    assert "Deaths: 2" in run(fake, "stats")[0]
    assert run(fake, "stats", {"player": "Nobody"})[0] == (
        "Nobody called Nobody has played on Test Server."
    )
    assert "isn't a Minecraft name" in run(fake, "stats", {"player": "a b"})[0]
    answer = agent.get("/api/servers/test/players/Alex/stats").json()
    assert answer["source"] == "files" and answer["values"] == {"deaths": 2, "mobs": 9}
    assert agent.get("/api/servers/test/players/Nobody/stats").status_code == 404


def test_bedrock_stats_come_from_the_console_and_say_so(fake, tmp_path, monkeypatch):  # noqa: F811
    client = signed_in(build_bedrock_config(tmp_path), monkeypatch)
    try:
        wait_for(lambda: client.get("/api/discord").json()["state"] == "connected")
        set_up(client, ["bedrock"])
        played(core_of(client), "Steve", "2533274474361790", 3, "bedrock")
        words = run(fake, "stats", {"player": "Steve"})[0].splitlines()
        assert words == [
            "**Steve** on Bedrock, from what the console showed",
            "Playtime: 3 h 0 min",
            "Joins: 3",
            "Bedrock doesn't keep stats files, so only joins and playtime are shown.",
        ]
    finally:
        client.__exit__(None, None, None)


# ------------------------------------------------------------ Levels
def test_the_level_curve():
    assert [levels.level_of(x) for x in (0, 7, 8, 199, 200, 800, 5000)] == [0, 0, 1, 4, 5, 10, 25]
    assert levels.xp_for(5) == 200  # 20 hours played


def test_xp_adds_up_across_alts_and_events_count_once(tmp_path):
    from agent.database.db import Database
    from agent.discord.links import Links

    db = Database(tmp_path / "db.sqlite")
    db.migrate()
    for name, uuid, server, hours in (("Alex", ALEX, "a", 2), ("Alt", ALT, "b", 1.5)):
        db.execute(
            "INSERT INTO players (server_id, username, uuid, total_seconds) VALUES (?, ?, ?, ?)",
            (server, name, uuid, hours * 3600),
        )
        Links(db).link(uuid, name, "java", server, MEMBER_ID, "sam", "test")
    db.execute(
        "INSERT INTO players (server_id, username, uuid, total_seconds, online, session_started) "
        "VALUES ('b', 'Alex', ?, 0, 1, ?)",
        (ALEX, time.time() - 1800),  # playing right now, for half an hour
    )
    assert levels.all_xp(db) == {MEMBER_ID: 40}
    assert levels.award(db, MEMBER_ID, 50, "event", "7")
    assert not levels.award(db, MEMBER_ID, 50, "event", "7")
    assert levels.all_xp(db) == {MEMBER_ID: 90}
    db.close()


def test_level_shows_level_xp_and_the_next_role(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    assert "Link your Minecraft account" in run(fake, "level")[0]
    played(core, "Alex", ALEX, 12)
    linked(core, "Alex", ALEX)
    words = run(fake, "level")[0].splitlines()
    assert words[0] == "You're Level 3 with 120 XP. Level 4 at 128 XP."
    assert words[1] == "Next role: Regular at Level 5."
    # A member who joined after their Level last changed gets their role here.
    core.db.execute("UPDATE players SET total_seconds = 20 * 3600 WHERE uuid = ?", (ALEX,))
    assert run(fake, "level")[-1].startswith("You're Level 5")
    assert fake.members[MEMBER_ID] == [level_role(core, 5)]


def test_level_roles_replace_each_other_and_level_ups_are_posted(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    played(core, "Alex", ALEX, 1)
    linked(core, "Alex", ALEX)
    call(agent, core.discord.update_levels)
    assert fake.members[MEMBER_ID] == []  # Level 1: no role yet, and no post (first seen)
    assert fake.in_channel("quests") == []
    calls = len(fake.calls)
    call(agent, core.discord.update_levels)
    assert len(fake.calls) == calls  # nothing changed, Discord isn't asked
    core.db.execute("UPDATE players SET total_seconds = 20 * 3600 WHERE uuid = ?", (ALEX,))
    call(agent, core.discord.update_levels)
    assert fake.members[MEMBER_ID] == [level_role(core, 5)]
    posted = fake.in_channel("quests")
    assert (
        posted[-1]["content"] == f"<@{MEMBER_ID}> reached **Level 5**! They're a **Regular** now."
    )
    assert posted[-1]["allowed_mentions"] == {"parse": []}
    core.db.execute("UPDATE players SET total_seconds = 80 * 3600 WHERE uuid = ?", (ALEX,))
    call(agent, core.discord.update_levels)
    assert fake.members[MEMBER_ID] == [level_role(core, 10)]  # Regular taken away
    # Unlinked: back to no Level role, and nothing posted for going down.
    posts = len(fake.in_channel("quests"))
    core.links.unlink(ALEX)
    call(agent, core.discord.update_levels)
    assert fake.members[MEMBER_ID] == [] and len(fake.in_channel("quests")) == posts


def test_level_ups_can_be_turned_off_and_strangers_are_skipped(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    answer = agent.put(f"/api/discord/guilds/{GUILD_ID}/levels", json={"announce": False})
    assert answer.status_code == 200 and answer.json()["announce"] is False
    played(core, "Alex", ALEX, 1)
    played(core, "Bob", ALT, 1)
    linked(core, "Alex", ALEX)
    linked(core, "Bob", ALT, member=OTHER)  # not in the Discord server
    call(agent, core.discord.update_levels)
    core.db.execute("UPDATE players SET total_seconds = 30 * 3600")
    call(agent, core.discord.update_levels)
    assert fake.members[MEMBER_ID] == [level_role(core, 5)]
    assert OTHER not in fake.members and fake.in_channel("quests") == []


def test_level_roles_can_be_changed_but_never_to_a_powerful_role(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    fake.roles += [
        {"id": "2000000000000000050", "name": "Kicker", "position": 1, "permissions": str(1 << 1)},
        {"id": "2000000000000000051", "name": "Builder", "position": 1, "permissions": "1024"},
    ]
    path = f"/api/discord/guilds/{GUILD_ID}/levels"
    for role_id, words in (
        ("2000000000000000050", "gives power in Discord"),
        (GUILD_ID, "can't be a Level role"),
        (layout.our_roles(core.db, GUILD_ID)["mod"], "can't be a Level role"),
    ):
        answer = agent.put(path, json={"roles": [{"level": 3, "role_id": role_id}]})
        assert answer.status_code == 400 and words in answer.json()["detail"], answer.text
    twice = [{"level": 3, "role_id": "2000000000000000051"}] * 2
    assert agent.put(path, json={"roles": twice}).status_code == 400
    answer = agent.put(
        path,
        json={"roles": [{"level": 3, "role_id": "2000000000000000051"}], "leaderboard_day": 4},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["roles"] == [
        {"level": 3, "role_id": "2000000000000000051", "name": "Builder"}
    ]
    assert answer.json()["leaderboard_day"] == 4
    # Setup doesn't bring the old Level roles back once they were changed.
    set_up(agent)
    assert layout.level_roles(core.db, GUILD_ID) == [(3, "2000000000000000051")]
    # A role given powers in Discord afterwards isn't handed out, and the
    # checks say so.
    fake.roles[-1]["permissions"] = str(1 << 3)
    played(core, "Alex", ALEX, 10)
    linked(core, "Alex", ALEX)
    call(agent, core.discord.update_levels)
    assert fake.members[MEMBER_ID] == []
    call(agent, core.discord.check)
    problems = agent.get("/api/discord").json()["guilds"][0]["report"]["problems"]
    assert {"id": "level_role_powers", "role": "Builder"} in problems


def test_level_settings_need_quests_and_events_on_that_discord_servers_server(
    agent,  # noqa: F811
    fake,  # noqa: F811
):
    set_up(agent)
    owner = dict(agent.headers)
    sam = add_helper(agent, owner, servers=["test"])
    path = f"/api/discord/guilds/{GUILD_ID}/levels"
    assert agent.get(path, headers=sam).status_code == 403
    given = agent.put(
        "/api/accounts/sam", json={"grants": {"test": ["quests.manage"]}}, headers=owner
    )
    assert given.status_code == 200, given.text
    assert agent.get(path, headers=sam).status_code == 200
    assert agent.put(path, json={"announce": False}, headers=sam).status_code == 200
    # Never the rest of the bot's settings.
    assert agent.get("/api/discord", headers=sam).status_code == 403


def test_linked_accounts_show_the_members_level(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    played(core, "Alex", ALEX, 20)
    linked(core, "Alex", ALEX)
    row = agent.get("/api/servers/test/discord-links").json()["links"][0]
    assert (row["level"], row["xp"], row["next_level_xp"]) == (5, 200, 288)


# ------------------------------------------------------------ the weekly leaderboard
def test_the_leaderboard_is_posted_once_on_its_day(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    today = datetime.datetime.now()
    evening = today.replace(hour=18, minute=30)
    now = evening.timestamp()
    for name, joined, left in (("Alex", now - 7200, now - 3600), ("Bob", now - 600, None)):
        core.db.execute(
            "INSERT INTO player_sessions (server_id, username, joined_at, left_at) "
            "VALUES ('test', ?, ?, ?)",
            (name, joined, left),
        )
    core.db.execute(
        "INSERT INTO player_sessions (server_id, username, joined_at, left_at) "
        "VALUES ('test', 'Old', ?, ?)",
        (now - 30 * 86400, now - 29 * 86400),  # last month: not this week
    )
    played(core, "Alex", ALEX, 20)
    linked(core, "Alex", ALEX)
    call(agent, core.discord.post_leaderboards, evening)
    assert fake.in_channel("quests") == []  # off by default
    agent.put(f"/api/discord/guilds/{GUILD_ID}/levels", json={"leaderboard_day": today.weekday()})
    call(agent, core.discord.post_leaderboards, today.replace(hour=9))
    assert fake.in_channel("quests") == []  # not before 18:00
    call(agent, core.discord.post_leaderboards, evening)
    call(agent, core.discord.post_leaderboards, evening.replace(hour=21))
    posted = fake.in_channel("quests")
    assert len(posted) == 1 and posted[0]["allowed_mentions"] == {"parse": []}
    lines = posted[0]["content"].splitlines()
    assert lines[0] == "**This week on Test Server**"
    assert lines[3:5] == ["1. Alex: 1 h 0 min", "2. Bob: 10 min"]
    assert f"1. <@{MEMBER_ID}>: Level 5" in lines


def test_changing_the_level_roles_sets_everyones_again_quietly(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    played(core, "Alex", ALEX, 20)
    linked(core, "Alex", ALEX)
    call(agent, core.discord.update_levels)
    assert fake.members[MEMBER_ID] == [level_role(core, 5)]
    fake.roles.append(
        {"id": "2000000000000000051", "name": "Builder", "position": 1, "permissions": "0"}
    )
    answer = agent.put(
        f"/api/discord/guilds/{GUILD_ID}/levels",
        json={"roles": [{"level": 2, "role_id": "2000000000000000051"}]},
    )
    assert answer.status_code == 200, answer.text
    wait_for(lambda: fake.members[MEMBER_ID] == ["2000000000000000051"])
    assert fake.in_channel("quests") == []  # no level-up posts for a resync
