"""The bot's part 7, against the fake Discord and the fake Minecraft server:
events (making, Going / Maybe / Can't, the reminder, starting the server
before), attendance from real data (advancement times, 15 minutes online, a
list to approve), fixes for 7 days, event rewards, and polls."""

from __future__ import annotations

import datetime
import time

import pytest

from agent import game_events, levels
from agent.addons import world_path
from agent.discord.bot import COMMANDS, command_list
from agent.game_events import check_event, check_poll, parse_time
from agent.quests import QuestError
from agent.security import permissions as P
from agent.stats import earned_at
from tests.conftest import build_bedrock_config
from tests.fake_discord import GUILD_ID, MEMBER_ID, wait_for
from tests.test_discord import (  # noqa: F401  (agent and fake are fixtures)
    MOD_ROLE_OF_OWNER,
    agent,
    core_of,
    fake,
    run,
    set_up,
    signed_in,
)
from tests.test_discord_levels import ALEX, ALT, OTHER, call, linked, played, write_stats
from tests.test_discord_quests import rewards, slayer
from tests.test_helpers import add_helper

SAM = MEMBER_ID
BOB = OTHER
BOB_UUID = "22222222-3333-4444-5555-666666666666"
HOUR = 3600.0


def make_event(agent, **wanted):  # noqa: F811
    soon = time.time() + 2 * HOUR
    body = {"title": "Dragon night", "starts_at": soon, "ends_at": soon + HOUR, **wanted}
    answer = agent.post("/api/servers/test/quests/events", json=body)
    assert answer.status_code == 200, answer.text
    return answer.json()["events"][-1]


def move(core, event_id, starts, ends):
    """The event happened at another time (the tests can't wait an hour)."""
    core.db.execute(
        "UPDATE game_events SET starts_at = ?, ends_at = ? WHERE id = ?", (starts, ends, event_id)
    )


def session(core, name, joined, left, server_id="test"):
    core.db.execute(
        "INSERT INTO player_sessions (server_id, username, joined_at, left_at) VALUES (?, ?, ?, ?)",
        (server_id, name, joined, left),
    )


def check(agent, now=None):  # noqa: F811
    core = core_of(agent)
    call(agent, core.game_events.check, now)


def update(agent):  # noqa: F811
    core = core_of(agent)
    call(agent, core.discord.update_events)


def page(agent):  # noqa: F811
    return agent.get("/api/servers/test/quests/events").json()


def pressed(fake, custom_id, **kwargs) -> str:  # noqa: F811
    """A button pressed once; what the presser was told."""
    token = fake.press(custom_id, **kwargs)
    return wait_for(lambda: fake.replies(token))[-1]


def when(timestamp):
    return time.strftime("%Y-%m-%d %H:%M:%S +0000", time.gmtime(timestamp))


# ------------------------------------------------------------ what an event may hold
def test_event_and_poll_values_are_checked(agent):  # noqa: F811
    ctx = core_of(agent).servers["test"]
    soon = time.time() + HOUR
    made = check_event(
        ctx,
        {
            "title": " Boss\nfight ",
            "kind": "advancement",
            "goal": "end/kill_dragon",
            "starts_at": soon,
            "ends_at": soon + HOUR,
            "rewards": [{"kind": "item", "item": "diamond", "count": 3}],
        },
    )
    assert (made["title"], made["goal"]) == ("Boss fight", "minecraft:end/kill_dragon")
    for wrong, words in (
        ({"title": ""}, "title"),
        ({"kind": "teleport"}, "Pick how"),
        ({"starts_at": time.time() - 60}, "already gone"),
        ({"starts_at": time.time() + 400 * 86400}, "a year"),
        ({"ends_at": soon + 60}, "15 minutes to 24 hours"),
        ({"ends_at": soon + 25 * HOUR}, "15 minutes to 24 hours"),
        ({"kind": "advancement", "goal": "../x y"}, "advancement"),
        ({"rewards": [{"kind": "command", "command": "op Alex"}]}, "reward"),
    ):
        with pytest.raises(QuestError, match=words):
            check_event(ctx, {"title": "A", "starts_at": soon, "ends_at": soon + HOUR, **wrong})
    later = time.time() + HOUR
    for wrong, words in (
        ({"answers": ["Yes"]}, "2 to 5"),
        ({"answers": list("abcdef")}, "2 to 5"),
        ({"answers": ["Yes", "yes"]}, "the same"),
        ({"question": ""}, "question"),
        ({"closes_at": time.time() + 60}, "10 minutes to 7 days"),
    ):
        with pytest.raises(QuestError, match=words):
            check_poll({"question": "Q?", "answers": ["A", "B"], "closes_at": later, **wrong})
    assert check_poll({"question": "Q", "answers": ["A", " ", "B"], "closes_at": later})[
        "answers"
    ] == ["A", "B"]
    with pytest.raises(QuestError, match="2026-10-30 20:00"):
        parse_time("tomorrow")
    assert parse_time("2026-10-30  20:00") == time.mktime((2026, 10, 30, 20, 0, 0, 0, 0, -1))


def test_bedrock_events_count_time_online_or_approval(fake, tmp_path, monkeypatch):  # noqa: F811
    client = signed_in(build_bedrock_config(tmp_path), monkeypatch)
    try:
        sid = next(iter(core_of(client).servers))
        assert client.get(f"/api/servers/{sid}/quests/events").json()["kinds"] == [
            "online",
            "approval",
        ]
        soon = time.time() + HOUR
        answer = client.post(
            f"/api/servers/{sid}/quests/events",
            json={
                "title": "A",
                "kind": "advancement",
                "goal": "end/kill_dragon",
                "starts_at": soon,
                "ends_at": soon + HOUR,
            },
        )
        assert answer.status_code == 400 and "advancement files" in answer.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_advancement_times_are_read_never_guessed():
    earned = {
        "minecraft:end/kill_dragon": {
            "criteria": {"killed_dragon": "2026-10-23 21:04:05 +0000"},
            "done": True,
        },
        "minecraft:story/mine_stone": {"criteria": {"a": "not a time"}, "done": True},
        "minecraft:story/smelt_iron": {"criteria": {"a": "2026-10-23 21:04:05 +0000"}},
    }
    assert earned_at(earned, "minecraft:end/kill_dragon") == (
        datetime.datetime(2026, 10, 23, 21, 4, 5, tzinfo=datetime.UTC).timestamp()
    )
    assert earned_at(earned, "minecraft:story/mine_stone") is None
    assert earned_at(earned, "minecraft:story/smelt_iron") is None  # not done
    assert earned_at(earned, "minecraft:nope") is None


# ------------------------------------------------------------ the post and the answers
def test_an_event_is_posted_with_buttons_and_answers_are_counted(agent, fake):  # noqa: F811
    set_up(agent)
    event = make_event(agent, details="Bring armour")
    update(agent)
    posted = fake.in_channel("events")
    assert len(posted) == 1 and "**Dragon night** on Test Server" in posted[0]["content"]
    assert "To count as there: be online at least 15 minutes" in posted[0]["content"]
    assert "For coming: 50 Level XP" in posted[0]["content"]
    assert posted[0]["allowed_mentions"] == {"parse": []}
    buttons = [b["custom_id"] for b in posted[0]["components"][0]["components"]]
    assert buttons == [f"rsvp_{a}:test:{event['id']}" for a in ("going", "maybe", "no")]
    # Not linked yet: the answer counts, with a hint.
    words = pressed(fake, buttons[0])
    assert "Got it: going" in words and "/link" in words
    linked(core_of(agent), "Alex", ALEX)
    pressed(fake, buttons[1], member=BOB, username="bob")
    assert "can't come" in pressed(fake, buttons[2], member=BOB, username="bob")  # changed
    wait_for(lambda: "Going 1 · Maybe 0 · Can't 1" in fake.in_channel("events")[0]["content"])
    assert len(fake.in_channel("events")) == 1  # edited, not posted again
    found = page(agent)["events"][0]
    assert found["answers"] == {"going": 1, "maybe": 0, "no": 1}
    assert [g["discord_name"] for g in found["going"]] == ["sam"]
    calls = len(fake.calls)
    update(agent)
    assert not [c for c in fake.calls[calls:] if c[0] == "PATCH"]  # nothing changed: no edit


def test_called_off_events_lose_their_buttons_and_take_no_answers(agent, fake):  # noqa: F811
    set_up(agent)
    event = make_event(agent)
    update(agent)
    answer = agent.delete(f"/api/servers/test/quests/events/{event['id']}")
    assert answer.status_code == 200 and answer.json()["events"][0]["state"] == "cancelled"
    wait_for(lambda: "Called off" in fake.in_channel("events")[0]["content"])
    assert fake.in_channel("events")[0]["components"] == []
    assert "called off" in pressed(fake, f"rsvp_going:test:{event['id']}")
    assert agent.delete(f"/api/servers/test/quests/events/{event['id']}").status_code == 400
    # An event of another server can't be answered through this one's id.
    assert "isn't there any more" in pressed(fake, "rsvp_going:other:1")


def test_the_reminder_pings_only_going_members_once(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    event = make_event(agent)
    for member, answer in ((SAM, "going"), (BOB, "maybe")):
        core.game_events.rsvp("test", event["id"], member, "x", answer)
    update(agent)
    assert not [m for m in fake.in_channel("events") if "starts" in m["content"]]
    move(core, event["id"], time.time() + 30 * 60, time.time() + 90 * 60)
    core.db.execute("UPDATE game_events SET created_at = created_at - 7200")
    update(agent)
    update(agent)
    told = [m for m in fake.in_channel("events") if "starts <t:" in m["content"]]
    assert len(told) == 1
    assert told[0]["content"].endswith(f"<@{SAM}>")
    assert told[0]["allowed_mentions"] == {"users": [SAM]}


def test_a_private_reminder_falls_back_to_the_ping(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    answer = agent.put(f"/api/discord/guilds/{GUILD_ID}/options", json={"reminders": "private"})
    assert answer.status_code == 200
    options = answer.json()["guilds"][0]["options"]
    assert options == {"remove_on_leave": False, "reminders": "private"}  # the other kept
    fake.blocks_dms.add(BOB)
    event = make_event(agent)
    for member in (SAM, BOB):
        core.game_events.rsvp("test", event["id"], member, "x", "going")
    move(core, event["id"], time.time() + 30 * 60, time.time() + 90 * 60)
    core.db.execute("UPDATE game_events SET created_at = created_at - 7200")
    update(agent)
    private = [m for m in fake.messages if m["channel_id"] == f"dm{SAM}"]
    assert len(private) == 1 and "You said you're going" in private[0]["content"]
    assert private[0]["allowed_mentions"] == {"parse": []}
    told = [m for m in fake.in_channel("events") if "starts <t:" in m["content"]]
    assert len(told) == 1 and told[0]["allowed_mentions"] == {"users": [BOB]}
    assert (
        agent.put(f"/api/discord/guilds/{GUILD_ID}/options", json={"reminders": "loud"}).status_code
        == 422
    )


def test_an_event_made_less_than_an_hour_ahead_has_no_reminder(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    soon = time.time() + 20 * 60
    event = make_event(agent, starts_at=soon, ends_at=soon + HOUR)
    core.game_events.rsvp("test", event["id"], SAM, "sam", "going")
    update(agent)
    assert not [m for m in fake.in_channel("events") if "starts <t:" in m["content"]]


def test_the_server_starts_ten_minutes_before_once(agent, fake):  # noqa: F811
    core = core_of(agent)
    ctx = core.servers["test"]
    event = make_event(agent, start_server=True)
    check(agent)
    assert ctx.server.state.value == "OFFLINE"
    move(core, event["id"], time.time() + 5 * 60, time.time() + HOUR)
    check(agent)
    wait_for(lambda: ctx.server.state.value == "ONLINE", 20)
    check(agent)  # once: a server stopped after this isn't started again
    started = core.db.query("SELECT message FROM events WHERE type = 'schedule'")
    assert [e["message"] for e in started] == ["Started for the event Dragon night"]


def test_maintenance_mode_skips_the_start(agent, fake):  # noqa: F811
    core = core_of(agent)
    ctx = core.servers["test"]
    event = make_event(agent, start_server=True)
    ctx.server.maintenance = True
    move(core, event["id"], time.time() + 60, time.time() + HOUR)
    check(agent)
    assert ctx.server.state.value == "OFFLINE"
    assert core.db.query("SELECT 1 FROM events WHERE message LIKE 'Skipped: maintenance%'")


# ------------------------------------------------------------ attendance
def test_fifteen_minutes_online_inside_the_event_counts(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    played(core, "Alex", ALEX, 0)
    played(core, "Alt", ALT, 0)
    played(core, "Bob", BOB_UUID, 0)
    linked(core, "Alex", ALEX)
    linked(core, "Alt", ALT)
    linked(core, "Bob", BOB_UUID, member=BOB)
    event = make_event(agent, rewards=[{"kind": "level_xp", "amount": 20}])
    core.game_events.rsvp("test", event["id"], BOB, "bob", "going")
    start = time.time() - 2 * HOUR
    move(core, event["id"], start, start + HOUR)
    # Sam: 10 minutes on Alex and 6 on the alt add up. Bob: 20 minutes, but
    # 10 of them before the start.
    session(core, "Alex", start - 600, start + 600)
    session(core, "Alt", start + 1800, start + 1800 + 360)
    session(core, "Bob", start - 600, start + 600)
    check(agent, start + HOUR + 10)  # just after the end: not counted yet
    assert page(agent)["events"][0]["state"] == "counting"
    check(agent)
    found = page(agent)["events"][0]
    assert found["state"] == "ended"
    came = {a["discord_id"]: (a["attended"], a["minutes"]) for a in found["attendance"]}
    assert came == {SAM: (True, 16), BOB: (False, 10)}
    assert levels.all_xp(core.db)[SAM] == 70  # 50 for coming and the event's 20
    assert BOB not in levels.all_xp(core.db) or levels.all_xp(core.db)[BOB] == 0
    check(agent)  # counted once
    assert levels.all_xp(core.db)[SAM] == 70
    update(agent)
    assert "Ended. 1 came." in fake.in_channel("events")[0]["content"]


def test_an_advancement_earned_inside_the_event_counts(agent, fake):  # noqa: F811
    set_up(agent)
    slayer(fake)
    core = core_of(agent)
    world = world_path(core.servers["test"])
    played(core, "Alex", ALEX, 0)
    played(core, "Bob", BOB_UUID, 0)
    linked(core, "Alex", ALEX)
    linked(core, "Bob", BOB_UUID, member=BOB)
    fake.members[BOB] = []
    event = make_event(
        agent,
        kind="advancement",
        goal="end/kill_dragon",
        rewards=[
            {"kind": "role", "role_id": "2000000000000000060"},
            {"kind": "item", "item": "cake", "count": 1},
        ],
    )
    start = time.time() - 2 * HOUR
    move(core, event["id"], start, start + HOUR)
    session(core, "Alex", start, start + HOUR)
    session(core, "Bob", start, start + HOUR)
    goal = "minecraft:end/kill_dragon"
    inside = {goal: {"criteria": {"killed_dragon": when(start + 900)}, "done": True}}
    before = {goal: {"criteria": {"killed_dragon": when(start - 86400)}, "done": True}}
    write_stats(world, ALEX, advancements=inside)
    write_stats(world, BOB_UUID, advancements=before)
    check(agent)
    came = {a["discord_id"]: a["attended"] for a in page(agent)["events"][0]["attendance"]}
    assert came == {SAM: True, BOB: False}
    # The role from the bot, the cake waiting for Alex's next join.
    call(agent, core.discord.give_roles)
    assert fake.members[SAM] == ["2000000000000000060"] and fake.members[BOB] == []
    waiting = rewards(core, "waiting")
    assert [(r["kind"], r["event_id"]) for r in waiting] == [("item", event["id"])]
    assert [r["title"] for r in core.quests.rewards("test", ("waiting",))] == ["Dragon night"]


def test_an_approval_list_waits_and_fixes_are_logged(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    played(core, "Alex", ALEX, 0)
    played(core, "Bob", BOB_UUID, 0)
    linked(core, "Alex", ALEX)
    linked(core, "Bob", BOB_UUID, member=BOB)
    event = make_event(agent, kind="approval", title="Build contest")
    core.game_events.rsvp("test", event["id"], BOB, "bob", "going")
    start = time.time() - 2 * HOUR
    move(core, event["id"], start, start + HOUR)
    session(core, "Alex", start, start + 1200)
    check(agent)
    found = page(agent)["events"][0]
    assert found["state"] == "approving"
    assert {a["discord_id"]: (a["attended"], a["how"]) for a in found["attendance"]} == {
        SAM: (True, "suggested"),
        BOB: (False, "suggested"),
    }
    assert levels.all_xp(core.db).get(SAM, 0) == 0  # nothing before the OK
    wait_for(lambda: core.db.query("SELECT 1 FROM events WHERE type = 'event_attendance'"))
    fixed = agent.put(
        f"/api/servers/test/quests/events/{event['id']}/attendance/{BOB}", json={"attended": True}
    )
    assert fixed.status_code == 200
    assert agent.post(f"/api/servers/test/quests/events/{event['id']}/approve").status_code == 200
    assert agent.post(f"/api/servers/test/quests/events/{event['id']}/approve").status_code == 400
    xp = levels.all_xp(core.db)
    assert xp[SAM] == 50 and xp[BOB] == 50
    # Taken back after the OK: the Level XP goes, logged with who did it.
    agent.put(
        f"/api/servers/test/quests/events/{event['id']}/attendance/{BOB}", json={"attended": False}
    )
    assert levels.all_xp(core.db).get(BOB, 0) == 0
    logged = core.db.query(
        "SELECT * FROM audit_log WHERE action = 'event_attendance_fix' ORDER BY id"
    )
    assert len(logged) == 2 and logged[-1]["detail"].endswith("did not come")
    # Ticking twice gives once.
    for _ in range(2):
        agent.put(
            f"/api/servers/test/quests/events/{event['id']}/attendance/{BOB}",
            json={"attended": True},
        )
    assert levels.all_xp(core.db)[BOB] == 50
    assert (
        agent.put(
            f"/api/servers/test/quests/events/{event['id']}/attendance/999", json={"attended": True}
        ).status_code
        == 400
    )


def test_attendance_can_only_be_fixed_for_seven_days(agent, fake):  # noqa: F811
    core = core_of(agent)
    event = make_event(agent)
    start = time.time() - 7 * 86400 - 2 * HOUR
    move(core, event["id"], start, start + HOUR)
    check(agent)
    found = page(agent)["events"][0]
    assert found["state"] == "ended" and found["fixable"] is False
    core.db.execute(
        "INSERT INTO event_attendance (event_id, discord_id, discord_name, how) "
        "VALUES (?, ?, 'sam', 'measured')",
        (event["id"], SAM),
    )
    answer = agent.put(
        f"/api/servers/test/quests/events/{event['id']}/attendance/{SAM}", json={"attended": True}
    )
    assert answer.status_code == 400 and "7 days" in answer.json()["detail"]


def test_a_discord_server_set_up_later_doesnt_get_old_events(agent, fake):  # noqa: F811
    core = core_of(agent)
    event = make_event(agent)
    start = time.time() - 2 * HOUR
    move(core, event["id"], start, start + HOUR)
    check(agent)
    set_up(agent)
    update(agent)
    assert fake.in_channel("events") == []


# ------------------------------------------------------------ making from Discord
def test_event_create_needs_quests_and_events(agent, fake):  # noqa: F811
    set_up(agent)
    options = {
        "title": "Race",
        "start": time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() + 2 * HOUR)),
        "minutes": 30,
        "level_xp": 10,
    }
    assert "don't allow" in run(fake, "event create", options)[-1]
    agent.put(
        f"/api/discord/guilds/{GUILD_ID}/roles/{MOD_ROLE_OF_OWNER}",
        json={"role_name": "Friends of Sam", "grants": {"test": ["quests.manage"]}},
    )
    words = run(fake, "event create", options, roles=[MOD_ROLE_OF_OWNER])[-1]
    assert words.startswith("Made the event Race")
    made = page(agent)["events"][0]
    assert made["ends_at"] - made["starts_at"] == 1800 and made["kind"] == "online"
    assert made["rewards"] == [{"kind": "level_xp", "amount": 10}]
    assert made["created_by"] == "sam (Discord)"
    wait_for(lambda: fake.in_channel("events"))
    bad = run(fake, "event create", {**options, "start": "soon"}, roles=[MOD_ROLE_OF_OWNER])
    assert "2026-10-30 20:00" in bad[-1]


def test_events_and_polls_routes_need_quests_and_events(agent, fake):  # noqa: F811
    owner = dict(agent.headers)
    sam = add_helper(agent, owner, servers=["test"])
    assert agent.get("/api/servers/test/quests/events", headers=sam).status_code == 403
    assert agent.post("/api/servers/test/quests/polls", json={}, headers=sam).status_code in (
        403,
        422,
    )
    agent.put("/api/accounts/sam", json={"grants": {"test": ["quests.manage"]}}, headers=owner)
    assert agent.get("/api/servers/test/quests/events", headers=sam).status_code == 200


def test_the_event_and_poll_commands_are_groups():
    registered = {c["name"]: c for c in command_list({"test": "Test Server"})}
    assert [o["name"] for o in registered["event"]["options"]] == ["create"]
    assert [o["name"] for o in registered["poll"]["options"]] == ["create"]
    for name in ("event create", "poll create"):
        assert COMMANDS[name].permission == P.QUESTS_MANAGE
    assert all(len(o["options"]) <= 25 for g in ("event", "poll") for o in registered[g]["options"])


# ------------------------------------------------------------ polls
def test_a_poll_takes_one_vote_each_and_posts_the_result(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    answer = agent.post(
        "/api/servers/test/quests/polls",
        json={
            "question": "Next map?",
            "answers": ["Desert", "Jungle", "Ice"],
            "closes_at": time.time() + HOUR,
        },
    )
    assert answer.status_code == 200, answer.text
    poll = answer.json()["polls"][0]
    posted = wait_for(lambda: fake.in_channel("events"))
    assert "**Next map?**" in posted[0]["content"] and "0 voted so far" in posted[0]["content"]
    buttons = [b["custom_id"] for b in posted[0]["components"][0]["components"]]
    assert buttons == [f"vote:test:{poll['id']}.{i}" for i in range(3)]
    assert "Your vote: Desert" in pressed(fake, buttons[0])
    pressed(fake, buttons[1])  # changed: still one vote
    pressed(fake, buttons[1], member=BOB, username="bob")
    assert "isn't an answer" in pressed(fake, f"vote:test:{poll['id']}.7")
    wait_for(lambda: "2 voted so far" in fake.in_channel("events")[0]["content"])
    # While open, the post shows no counts (early votes don't sway later ones).
    assert "Jungle: 2" not in fake.in_channel("events")[0]["content"]
    assert page(agent)["polls"][0]["votes"] == [0, 2, 0]
    core.db.execute("UPDATE polls SET closes_at = ? WHERE id = ?", (time.time() - 1, poll["id"]))
    check(agent)
    assert "has closed" in pressed(fake, buttons[2])
    update(agent)
    update(agent)
    shown = fake.in_channel("events")
    assert "Jungle: 2 (100%)" in shown[0]["content"] and shown[0]["components"] == []
    results = [m for m in shown if "won with" in m["content"]]
    assert len(results) == 1
    assert results[0]["content"] == (
        "The poll **Next map?** closed: **Jungle** won with 2 of 2 votes."
    )


def test_a_poll_closed_early_with_no_votes_says_so(agent, fake):  # noqa: F811
    set_up(agent)
    poll = agent.post(
        "/api/servers/test/quests/polls",
        json={"question": "Q", "answers": ["A", "B"], "closes_at": time.time() + HOUR},
    ).json()["polls"][0]
    assert agent.delete(f"/api/servers/test/quests/polls/{poll['id']}").status_code == 200
    assert agent.delete(f"/api/servers/test/quests/polls/{poll['id']}").status_code == 400
    wait_for(lambda: [m for m in fake.in_channel("events") if "Nobody voted" in m["content"]])


def test_poll_create_from_discord(agent, fake):  # noqa: F811
    set_up(agent)
    agent.put(
        f"/api/discord/guilds/{GUILD_ID}/roles/{MOD_ROLE_OF_OWNER}",
        json={"role_name": "Friends of Sam", "grants": {"test": ["quests.manage"]}},
    )
    options = {"question": "Reset the End?", "answer1": "Yes", "answer2": "No", "hours": 2}
    assert run(fake, "poll create", options, roles=[MOD_ROLE_OF_OWNER])[-1].startswith(
        "Made the poll"
    )
    made = page(agent)["polls"][0]
    assert made["answers"] == ["Yes", "No"]
    assert 2 * HOUR - 60 < made["closes_at"] - time.time() <= 2 * HOUR
    for _ in range(game_events.MAX_OPEN_POLLS - 1):
        agent.post(
            "/api/servers/test/quests/polls",
            json={"question": "Q", "answers": ["A", "B"], "closes_at": time.time() + HOUR},
        )
    assert "10 polls open" in run(fake, "poll create", options, roles=[MOD_ROLE_OF_OWNER])[-1]


def test_event_tables_go_in_export_everything():
    from agent.transfer import DATA_TABLES

    assert {"game_events", "event_rsvps", "event_attendance", "polls", "poll_votes"} <= set(
        DATA_TABLES
    )
    assert game_events.TRACKING_ADDONS == {}  # the hook is there; no add-on is needed
