"""What the bot says about players' progress: /stats, /level, level-ups and
the weekly leaderboard.

Stats from a world's files say when the file was saved (Discord shows the
time in each reader's own time zone), never that they're live. Names are
escaped so nobody's Minecraft name turns into Discord formatting, and
members are shown as mentions, which the bot posts with no pings allowed.
"""

from __future__ import annotations

from typing import Any

from .. import levels
from .bridge import escape
from .live import duration

LABELS = {
    "playtime": "Playtime",
    "deaths": "Deaths",
    "mobs": "Mobs killed",
    "blocks": "Blocks mined",
    "advancements": "Advancements",
    "joins": "Joins",
}


def stats(found: dict[str, Any], server: str) -> str:
    name = escape(found["player"])
    values, other = found["values"], found["other"]
    if found["source"] == "console":
        head = f"**{name}** on {server}, from what the console showed"
    elif found["saved_at"] is None:
        return (
            f"{name} has no stats saved on {server} yet. Minecraft writes them every few "
            "minutes while they play and when they leave."
        )
    else:
        head = f"**{name}** on {server}, last saved <t:{int(found['saved_at'])}:R>"
    lines = [head]
    for key, words in LABELS.items():
        if key in values:
            value = duration(values[key]) if key == "playtime" else f"{values[key]:,}"
            lines.append(f"{words}: {value}")
    lines += [f"{escape(x['name'])}: {x['value']:,}" for x in other]
    if found["source"] == "console":
        lines.append(f"{server} doesn't keep stats files, so only joins and playtime are shown.")
    elif len(lines) == 1:
        lines.append("The stats file has none of these numbers.")
    return "\n".join(lines)


def level(xp: int, roles: list[tuple[int, str]], names: dict[str, str]) -> str:
    """/level: the Level, the XP, what's next."""
    now = levels.standing(xp)
    lines = [
        f"You're Level {now['level']} with {xp:,} XP. "
        f"Level {now['level'] + 1} at {now['next_level_xp']:,} XP."
    ]
    nxt = next(((at, rid) for at, rid in roles if at > now["level"]), None)
    if nxt:
        lines.append(f"Next role: {escape(names.get(nxt[1], 'a Level role'))} at Level {nxt[0]}.")
    lines.append(
        f"XP comes from playing ({levels.XP_PER_HOUR} an hour, on all your linked accounts), "
        "events and quests."
    )
    return "\n".join(lines)


def level_up(member_id: str, level: int, role: str | None) -> str:
    words = f"<@{member_id}> reached **Level {level}**!"
    return f"{words} They're a **{escape(role)}** now." if role else words


def leaderboard(servers: str, played: list[dict[str, Any]], top: list[tuple[str, int]]) -> str:
    lines = [f"**This week on {servers}**", "", "Most played in the last 7 days:"]
    lines += [
        f"{i}. {escape(p['name'])}: {duration(p['seconds'])}" for i, p in enumerate(played, 1)
    ] or ["Nobody played this week."]
    lines += ["", "Highest Levels:"]
    lines += [
        f"{i}. <@{member}>: Level {levels.level_of(xp)}" for i, (member, xp) in enumerate(top, 1)
    ] or ["Nobody has linked an account yet. /link adds yours."]
    return "\n".join(lines)
