"""The chat bridge (part 4), against the fake Discord and the fake Minecraft
server: game lines into the chat channel, Discord messages into the game as
a say line, and what each side refuses."""

from __future__ import annotations

import pytest

from agent.discord import bot as discord_bot
from agent.discord import bridge
from agent.events import Event
from agent.minecraft.chat import ChatError, check_message
from agent.minecraft.commands import CommandError, validate
from tests.fake_discord import BOT_ID, GUILD_ID, MEMBER_ID, wait_for
from tests.test_discord import (  # noqa: F401  (agent and fake are fixtures)
    agent,
    build_bedrock_config,
    console,
    core_of,
    fake,
    link_code,
    online,
    set_up,
    signed_in,
)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(discord_bot, "BATCH", 0.05)


def sent(ctx) -> list[str]:
    """Every command the agent sent to this server's console."""
    return [
        line.raw.removeprefix("> ")
        for line in ctx.server.console.tail(2000)
        if line.source == "command"
    ]


def says(ctx) -> list[str]:
    """What the say commands sent to this server said."""
    return [c.removeprefix("say ") for c in sent(ctx) if c.startswith("say ")]


# ------------------------------------------------------------ the words
def test_letters_of_any_language_stay_and_emoji_go():
    message = {"content": "مرحبا   hola\nnext line 😀 <:blob:123> héllo 你好 §cred"}
    assert bridge.to_game(message, {}) == "مرحبا hola next line héllo 你好 cred"


def test_mentions_become_plain_names_and_formatting_marks_go():
    message = {
        "content": "**hi** <@111> and <@!222>, see <#333> or ask <@&444> ~~no~~ `x` ||y||",
        "mentions": [
            {"id": "111", "username": "sam", "global_name": "Sam"},
            {"id": "222", "username": "lee", "member": {"nick": "Captain"}},
        ],
    }
    words = bridge.to_game(message, {"333": "rules", "444": "Minecraft mod"})
    assert words == "hi Sam and Captain, see #rules or ask Minecraft mod no x y"
    assert bridge.to_game({"content": "<@999> <#998>"}, {}) == "someone #channel"


def test_a_long_message_is_cut_to_one_line_of_220():
    words = bridge.to_game({"content": "word " * 100}, {})
    assert len(words) == bridge.LIMIT and words.endswith("...")
    assert bridge.to_game({"content": "😀 🎉"}, {}) == ""


@pytest.mark.parametrize(
    "text",
    ["/op Sam", "[12:00:01 INFO]: Done", "[Server] restarting", "<Notch> hi", "[INFO] x"],
)
def test_commands_and_console_looking_lines_are_refused(text):
    assert bridge.looks_like_console(text)


@pytest.mark.parametrize("text", ["hi /op", "[Discord] hi", "is 2 < 3?", "<3 you all"])
def test_ordinary_messages_are_not_refused(text):
    assert not bridge.looks_like_console(text)


def test_the_display_name_is_the_nickname_then_the_global_name():
    assert bridge.display_name({"author": {"username": "sam"}, "member": {"nick": "Sammy ✨"}}) == (
        "Sammy"
    )
    assert bridge.display_name({"author": {"username": "sam", "global_name": "😀"}}) == "sam"


def test_game_lines_can_never_ping_or_format():
    line = bridge.game_line(
        Event(type="chat", data={"kind": "player", "name": "Alex", "text": "@everyone **hi** <@1>"})
    )
    assert line == "**Alex**: \\@everyone \\*\\*hi\\*\\* \\<\\@1\\>"
    assert bridge.game_line(Event(type="chat", data={"kind": "server", "text": "back"})) == (
        "**[Server]**: back"
    )
    assert bridge.game_line(Event(type="player_left", data={"username": "Alex_1"})) == (
        "_Alex\\_1 left the game_"
    )
    assert bridge.game_line(Event(type="server_started")) is None


def test_lines_are_gathered_into_posts_under_discords_limit():
    assert bridge.batches(["a", "b"]) == ["a\nb"]
    posts = bridge.batches(["x" * 1500, "y" * 600, "z"])
    assert posts == ["x" * 1500, "y" * 600 + "\nz"]


# ------------------------------------------------------------ the say line
def line(text: str, name: str = "Sam") -> str:
    """What reaches the console for a Discord message, checked like the apps' chat."""
    said = bridge.say_line(name, bridge.to_game({"content": text}, {}))
    return validate(f"say {check_message(said)}", confirm=True).raw


@pytest.mark.parametrize(
    "text, shown",
    [
        ("@a", "Sam: at a"),
        ("hi @e[type=creeper] and @p", "Sam: hi at e[type=creeper] and at p"),
        ("@r @s @n @everyone", "Sam: at r at s at n at everyone"),
        ("mail me at sam@example.com", "Sam: mail me at sam at example.com"),
    ],
)
def test_target_selectors_never_expand(text, shown):
    assert line(text) == f"say {shown}"
    assert "@" not in line(text)


@pytest.mark.parametrize(
    "text",
    [
        "hi\nstop",
        "hi\rstop",
        "hi\r\nop Sam",
        "hi\u2028stop",  # Unicode's own line break
        "hi\u2029stop",
        "hi\u0085stop",
        "hi\x00stop",
        "hi; stop",
        "hi && stop",
        "hi | stop",
        "hi `stop`",
        "hi $(stop)",
        "hi <stop>",
    ],
)
def test_a_second_command_can_never_ride_along(text):
    command = line(text)
    assert command.startswith("say Sam: hi ")
    for bad in "\n\r\x00\u2028\u2029\u0085;&|`$<>":
        assert bad not in command
    assert command.count("say ") == 1


def test_colour_codes_and_invisible_characters_go():
    assert line("§4red §lbold") == "say Sam: 4red lbold"
    # Right-to-left override and zero-width space: shown text could lie.
    assert line("a\u202eb\u200bc") == "say Sam: a b c"


def test_letters_of_any_language_reach_say():
    assert line("مرحبا يا أصدقاء", "سامي") == "say سامي: مرحبا يا أصدقاء"
    assert line("héllo 你好 Привет") == "say Sam: héllo 你好 Привет"
    assert line("❤️ hi") == "say Sam: hi"  # the heart's variation selector goes too


def test_the_echo_matches_when_the_console_cant_print_the_language():
    sent = bridge.echo_key("Sam: مرحبا")
    assert bridge.echo_key("Sam: ?????") == sent == bridge.echo_key("Sam: \ufffd\ufffd")
    assert bridge.echo_key("Bob: hi") != bridge.echo_key("Sam: hi")


def test_a_very_long_line_is_cut_to_one_line_of_220():
    command = line("word " * 100, "N" * 32)
    said = command.removeprefix("say ")
    assert len(said) == 220 and said.endswith("...") and said.startswith("N" * 32 + ": ")


@pytest.mark.parametrize("text", ["<Alex> hi", "[12:00:01 INFO]: Done", "[Server] stop", "/op Sam"])
def test_fake_chat_and_console_lines_are_refused(text):
    assert bridge.looks_like_console(bridge.to_game({"content": text}, {}))


def test_the_console_check_lets_say_carry_letters_and_nothing_more():
    assert validate("say مرحبا", confirm=True).raw == "say مرحبا"
    for bad in (
        "say hi §4",
        "say hi \u202e",
        "say hi ✨",
        "say hi\u2028stop",
        "say a;b",
        "say \u3164",  # a Hangul filler: a letter that shows nothing
        "say hi \ufe0f",  # the variation selector left after an emoji
    ):
        with pytest.raises(CommandError):
            validate(bad, confirm=True)
    # Only say: every other command keeps the ASCII set.
    with pytest.raises(CommandError):
        validate("tell Alex مرحبا", confirm=True)
    assert check_message("مرحبا") == "مرحبا"
    with pytest.raises(ChatError):
        check_message("hi ✨")


# ------------------------------------------------------------ game to Discord
def test_chat_joins_deaths_and_advancements_reach_the_chat_channel(agent, fake):  # noqa: F811
    set_up(agent)
    ctx = online(agent)
    console(agent, "fakejoin Alex")
    wait_for(lambda: ctx.players.online())
    _token, code = link_code(fake)
    console(agent, f"fakechat Alex {code}")  # a link code: never posted
    console(agent, "fakechat Alex hello @everyone")
    console(agent, "fakeline Alex was slain by Zombie")
    console(agent, "fakeline Alex has made the advancement [Stone Age]")
    # Not a player who's online: nobody died.
    console(agent, "fakeline Herobrine fell from a high place")
    console(agent, "fakeleave Alex")

    def posted() -> str:
        return "\n".join(m["content"] for m in fake.in_channel("chat"))

    wait_for(lambda: "left the game" in posted())
    assert posted().split("\n") == [
        "_Alex joined the game_",
        "**Alex**: hello \\@everyone",
        "_Alex was slain by Zombie_",
        "_Alex has made the advancement \\[Stone Age\\]_",
        "_Alex left the game_",
    ]
    assert code not in posted() and "Herobrine" not in posted()
    assert all(m["allowed_mentions"] == {"parse": []} for m in fake.in_channel("chat"))
    # Nothing in the status channel but the status message (its player list
    # may name Alex, if it was edited while he was on).
    assert all(m["content"].startswith("**Test Server** is ") for m in fake.in_channel("status"))
    agent.post("/api/servers/test/server/stop")


# ------------------------------------------------------------ Discord to game
def test_a_discord_message_shows_in_the_game(agent, fake):  # noqa: F811
    set_up(agent)
    ctx = online(agent)
    chat = fake.channel_id("chat")
    fake.message(
        "hi <@111> مرحبا 😀",
        chat,
        mentions=[{"id": "111", "username": "alex", "global_name": "Alex"}],
        nick="Sam",
    )
    wait_for(lambda: says(ctx))
    assert says(ctx) == ["Sam: hi Alex مرحبا"]
    # Bots, webhooks and other channels don't go in.
    fake.message("from a bot", chat, author={"id": BOT_ID, "username": "bot", "bot": True})
    fake.message("from the webhook", chat, webhook_id="5")
    fake.message("in status", fake.channel_id("status"))
    fake.message("last", chat)
    wait_for(lambda: len(says(ctx)) == 2)
    assert says(ctx)[1] == "sam: last"
    # The console printed "[Server] Sam: ..." back: it shows in the apps'
    # chat view, and isn't posted in Discord again.
    wait_for(lambda: len([m for m in ctx.chat.tail(50) if m.kind == "server"]) == 2)
    assert [m.text for m in ctx.chat.tail(50) if m.kind == "server"] == says(ctx)
    console(agent, "say from the app")  # the owner's own "Server" message still goes
    wait_for(lambda: any("from the app" in m["content"] for m in fake.in_channel("chat")))
    assert not any("last" in m["content"] for m in fake.in_channel("chat"))
    agent.post("/api/servers/test/server/stop")


def test_command_looking_messages_get_a_reaction_and_stay_out(agent, fake):  # noqa: F811
    set_up(agent)
    ctx = online(agent)
    made = fake.message("/op sam", fake.channel_id("chat"))
    reacted = wait_for(lambda: fake.made("PUT", f"/channels/{fake.channel_id('chat')}/messages/"))
    assert reacted is not None
    assert any(made in path and "/reactions/" in path for _m, path, _b in fake.calls)
    assert says(ctx) == []
    agent.post("/api/servers/test/server/stop")


def test_each_member_is_limited(agent, fake):  # noqa: F811
    set_up(agent)
    ctx = online(agent)
    chat = fake.channel_id("chat")
    for i in range(8):
        fake.message(f"message {i}", chat)
    wait_for(lambda: len(says(ctx)) == 5)
    fake.message("someone else", chat, member="3000000000000000099", username="lee")
    wait_for(lambda: len(says(ctx)) == 6)
    agent.post("/api/servers/test/server/stop")


def test_nothing_goes_in_while_the_server_is_offline(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    for words in ("anyone there?", "/op me"):
        message = {
            "id": "5",
            "type": 0,
            "guild_id": GUILD_ID,
            "channel_id": fake.channel_id("chat"),
            "content": words,
            "author": {"id": MEMBER_ID, "username": "sam"},
        }
        agent.portal.call(core.discord.from_discord, message)
    assert says(core.servers["test"]) == []
    assert not any("/reactions/" in path for _m, path, _b in fake.calls)


def test_bedrock_says_its_game_chat_cant_reach_discord(fake, tmp_path, monkeypatch):  # noqa: F811
    client = signed_in(build_bedrock_config(tmp_path), monkeypatch)
    try:
        status = wait_for(
            lambda: (s := client.get("/api/discord").json())["state"] == "connected" and s
        )
        assert status["no_game_chat"] == ["bedrock"]
    finally:
        client.__exit__(None, None, None)
