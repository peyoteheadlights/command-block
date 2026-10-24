"""The chat bridge's words: what a game line looks like in Discord, and what
a Discord message looks like in the game.

Game to Discord: chat, /me, the owner's "Server" messages, joins, leaves,
deaths and advancements. Nothing a player types can ping anyone (the bot
posts with no mentions allowed) or turn into Discord formatting.

Discord to game: one line of at most 220 characters, letters of any
language kept, emoji dropped, mentions as plain names and no formatting
marks. The tellraw command itself is built in agent/minecraft/chat.py, so
nothing in this folder reaches the console.
"""

from __future__ import annotations

import re
import unicodedata

from ..events import Event

LIMIT = 220  # characters of a Discord message shown in the game
POST_LIMIT = 2000  # characters in one Discord message

# Discord's own markup inside a message's text.
MENTION_RE = re.compile(r"<(@!?|@&|#)(\d+)>")
CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
MARKS_RE = re.compile(r"\*\*|__|~~|\|\||`")
# Characters kept: letters and their accents, digits, punctuation, spaces
# and maths and currency signs. Emoji are symbols ("So") and joiners
# ("Cf"), so they go; so does "§", Minecraft's colour-code sign.
KEPT = ("L", "M", "N", "P", "Zs", "Sm", "Sc")
# A message that starts like a command or a line of the server's console.
# In the game it would still show "[Discord] Sam:" in front, but nobody
# should be able to make it look like the server said something.
CONSOLE_RE = re.compile(
    r"^(?:/|\[\d{1,2}:\d{2}|\[(?:Server|Console|INFO|WARN|ERROR)\b|<[^>]{1,16}>)",
    re.IGNORECASE,
)
# Discord's markdown, escaped in game lines so they show as typed.
DISCORD_MARKS_RE = re.compile(r"([\\*_~`|<>\[\]()#@:-])")


# -- game to Discord -------------------------------------------------------------
def escape(text: str) -> str:
    return DISCORD_MARKS_RE.sub(r"\\\1", text)


def game_line(event: Event) -> str | None:
    """The line an event becomes in a chat channel, or None."""
    data = event.data or {}
    name = escape(str(data.get("username") or data.get("name") or ""))
    if event.type == "chat":
        said = escape(str(data.get("text") or ""))
        kind = data.get("kind")
        if kind == "player":
            return f"**{name}**: {said}"
        if kind == "action":
            return f"\\* {name} {said}"
        if kind == "server":
            return f"**[Server]**: {said}"
        return None
    if event.type == "player_joined":
        return f"_{name} joined the game_"
    if event.type == "player_left":
        return f"_{name} left the game_"
    if event.type in ("player_died", "player_advancement"):
        return f"_{escape(event.message)}_"
    return None


def batches(lines: list[str]) -> list[str]:
    """Lines gathered into as few posts as fit."""
    out: list[str] = []
    for line in lines:
        line = line[:POST_LIMIT]
        if out and len(out[-1]) + 1 + len(line) <= POST_LIMIT:
            out[-1] += "\n" + line
        else:
            out.append(line)
    return out


# -- Discord to game -------------------------------------------------------------
def plain(text: str) -> str:
    """Only the kept characters, on one line with single spaces."""
    kept = "".join(
        ch if unicodedata.category(ch).startswith(KEPT) and ch != "§" else " " for ch in text
    )
    return " ".join(kept.split())


def display_name(message: dict) -> str:
    author = message.get("author") or {}
    nick = (message.get("member") or {}).get("nick")
    for name in (nick, author.get("global_name"), author.get("username")):
        if name and (shown := plain(str(name))[:32]):
            return shown
    return "someone"


def to_game(message: dict, names: dict[str, str]) -> str:
    """A message's text as the game shows it ("" when nothing is left).
    ``names`` maps role and channel ids to their names (from the check)."""
    people = {
        str(u.get("id")): (u.get("member") or {}).get("nick")
        or u.get("global_name")
        or u.get("username")
        or "someone"
        for u in message.get("mentions") or []
    }

    def mention(m: re.Match) -> str:
        kind, wanted = m.group(1), m.group(2)
        if kind == "#":
            return "#" + names.get(wanted, "channel")
        if kind == "@&":
            return "@" + names.get(wanted, "role")
        return "@" + str(people.get(wanted, "someone"))

    text = CUSTOM_EMOJI_RE.sub(" ", str(message.get("content") or ""))
    text = MARKS_RE.sub("", MENTION_RE.sub(mention, text))
    text = plain(text)
    if len(text) > LIMIT:
        text = text[: LIMIT - 3].rstrip() + "..."
    return text


def looks_like_console(text: str) -> bool:
    return bool(CONSOLE_RE.match(text))
