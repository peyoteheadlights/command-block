"""The chat bridge's words: what a game line looks like in Discord, and what
a Discord message looks like in the game.

Game to Discord: chat, /me, the owner's "Server" messages, joins, leaves,
deaths and advancements. Nothing a player types can ping anyone (the bot
posts with no mentions allowed) or turn into Discord formatting.

Discord to game: one ``say`` line, "Sam: hi", of at most 220 characters:
letters of any language kept, emoji and symbols dropped, mentions as plain
names, no formatting marks and no "@", so a target selector like @a can
never expand. The say command itself is sent by agent/minecraft/chat.py,
through the console check, so nothing in this folder reaches the console.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata

from ..events import Event
from ..minecraft.chat import MESSAGE_CHAR_RE, MESSAGE_MAX
from ..minecraft.commands import any_language
from ..minecraft.console import ANSI_RE

LIMIT = MESSAGE_MAX  # characters of a Discord message shown in the game
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
    return batches_of(lines, POST_LIMIT)


# -- the console channel ----------------------------------------------------------
CONSOLE_LINE = 300  # characters of one console line shown
IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
# Something that may be an IPv6 address; checked with ipaddress before it's
# hidden, so a time like 12:34:56 stays.
IPV6_RE = re.compile(r"(?<![\w:])[0-9A-Fa-f:]*:[0-9A-Fa-f:]*:[0-9A-Fa-f]*(?:%\w+)?(?![\w:])")
# A Windows user folder names the person who owns the PC.
USER_FOLDER_RE = re.compile(r"([A-Za-z]:[\\/]Users[\\/])[^\\/\s]+", re.IGNORECASE)
# "<Alex> K7P2QX": a player typing a /link code. Never shown, used or not.
# Any 6 letters from the code alphabet go, which also hides a few real words.
CODE_LINE_RE = re.compile(r"<[^>]{1,20}> [A-HJKMNP-Z2-9]{6}\s*$", re.IGNORECASE)
HIDDEN = "[address hidden]"


def _ipv6(m: re.Match) -> str:
    found = m.group(0).split("%")[0]
    # Also with a port after it: 2001:db8::1:25565.
    for wanted in (found, found.rsplit(":", 1)[0]):
        try:
            ipaddress.IPv6Address(wanted)
        except ValueError:
            continue
        return HIDDEN
    return m.group(0)


def console_line(raw: str) -> str | None:
    """A console line as the read-only console channel shows it: addresses
    and the PC's user folder hidden, link codes left out, one capped line."""
    line = ANSI_RE.sub("", raw).replace("`", "'").strip()
    if not line or CODE_LINE_RE.search(line):
        return None
    line = USER_FOLDER_RE.sub(r"\1...", IPV6_RE.sub(_ipv6, IPV4_RE.sub(HIDDEN, line)))
    return line[:CONSOLE_LINE]


def console_posts(lines: list[str], left_out: int = 0) -> list[str]:
    """Console lines as code blocks (nothing in them is Discord formatting)."""
    if left_out:
        lines = [f"({left_out} lines left out: the console was too busy)", *lines]
    return [f"```\n{chunk}\n```" for chunk in batches_of(lines, POST_LIMIT - 8)]


def batches_of(lines: list[str], limit: int) -> list[str]:
    out: list[str] = []
    for line in lines:
        line = line[:limit]
        if out and len(out[-1]) + 1 + len(line) <= limit:
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
            return names.get(wanted, "role")
        return str(people.get(wanted, "someone"))

    text = CUSTOM_EMOJI_RE.sub(" ", str(message.get("content") or ""))
    text = MARKS_RE.sub("", MENTION_RE.sub(mention, text))
    text = plain(text)
    if len(text) > LIMIT:
        text = text[: LIMIT - 3].rstrip() + "..."
    return text


def looks_like_console(text: str) -> bool:
    return bool(CONSOLE_RE.match(text))


def say_line(name: str, text: str) -> str:
    """ "name: text" as the say command carries it: "@" becomes "at" (so
    @a, @e, @p, @r, @s and @n never expand), and anything say can't carry
    (like < > & ; | $ and symbols) becomes a space. "" when no words are left."""

    def sayable(words: str) -> str:
        words = re.sub(r"\s*@\s*", " at ", words)
        kept = "".join(ch if any_language(ch, MESSAGE_CHAR_RE) else " " for ch in words)
        return " ".join(kept.split())

    said, who = sayable(text), sayable(name) or "someone"
    if not said:
        return ""
    line = f"{who}: {said}"
    if len(line) > LIMIT:
        line = line[: LIMIT - 3].rstrip() + "..."
    return line
