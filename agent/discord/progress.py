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
from ..game_events import ATTEND_XP
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


# -- quests ------------------------------------------------------------------
REPEAT_WORDS = {
    "once": "",
    "daily": "every day",
    "weekly": "every week",
    "monthly": "every month",
    "yearly": "every year",
}


def short_id(name: str) -> str:
    return escape(name.removeprefix("minecraft:").replace("_", " "))


def task(quest: dict[str, Any]) -> str:
    """What the quest asks for, in a few words."""
    count, goal = quest["count"], quest["goal"]
    if quest["kind"] == "stat":
        section, _, name = goal.partition(" ")
        return f"{short_id(section)} {short_id(name)} ×{count:,}"
    return {
        "advancement": f"earn the advancement {short_id(goal)}",
        "playtime": f"play {count:,} h",
        "joins": f"join {count:,} times",
        "items": f"bring {count:,} {short_id(goal)} (/quest done)",
        "approval": "show the owner or a moderator (/quest done)",
    }[quest["kind"]]


def rewards(given: list[dict[str, Any]]) -> str:
    words = {
        "role": lambda r: f"<@&{r['role_id']}>",
        "item": lambda r: f"{r['count']} {short_id(r['item'])}",
        "xp": lambda r: f"{r['levels']} Minecraft XP levels",
        "level_xp": lambda r: f"{r['amount']} Level XP",
    }
    return ", ".join(words[r["kind"]](r) for r in given) or "bragging rights"


def needed(quest: dict[str, Any], progress: int) -> str:
    unit = " h" if quest["kind"] == "playtime" else ""
    return f"{min(progress, quest['count']):,}/{quest['count']:,}{unit}"


def quest_line(quest: dict[str, Any]) -> str:
    parts = [task(quest)]
    if REPEAT_WORDS[quest["repeat"]]:
        parts.append(REPEAT_WORDS[quest["repeat"]])
    if quest.get("ends_at"):
        parts.append(f"until <t:{int(quest['ends_at']) - 1}:f>")
    parts.append(f"reward: {rewards(quest['rewards'])}")
    return f"**{quest['id']}. {escape(quest['title'])}**: " + " · ".join(parts)


def board(servers: list[tuple[str, list[dict[str, Any]]]]) -> str:
    """The quests channel's message, edited when something changes."""
    lines = ["**Quests**", "Join one with /quest join and its number."]
    shown = 0
    for server, quests in servers:
        if not quests:
            continue
        lines += ["", f"__{escape(server)}__"]
        for quest in quests:
            shown += 1
            lines.append(quest_line(quest))
            if quest["details"]:
                lines.append(escape(quest["details"]))
            finished = [m for m in quest["members"] if m["finished_at"]]
            going = [m for m in quest["members"] if not m["finished_at"]][:3]
            if going:
                lines.append(
                    "Closest: "
                    + ", ".join(
                        f"<@{m['discord_id']}> {needed(quest, m['progress'])}" for m in going
                    )
                )
            if finished:
                lines.append("Done: " + ", ".join(f"<@{m['discord_id']}>" for m in finished[:10]))
            note = f"{quest['joined']} joined"
            if quest.get("saved_at"):
                note += f", progress as last saved <t:{int(quest['saved_at'])}:R>"
            lines.append(f"-# {note}")
    if not shown:
        lines += ["", "No quests right now. Got an idea? /quest suggest"]
    words = "\n".join(lines)
    return words if len(words) <= 2000 else words[:1950].rsplit("\n", 1)[0] + "\n…and more."


def my_quests(found: list[dict[str, Any]], server: str) -> str:
    """/quest progress: the member's quests on one server."""
    if not found:
        return f"You aren't on any quests on {server}. The quests channel lists them."
    lines = [f"Your quests on {server}:"]
    for quest in found:
        if quest["finished_at"]:
            state = "done!"
        elif quest["kind"] == "approval":
            state = "waiting for /quest done"
        else:
            state = needed(quest, quest["progress"])
        line = f"**{quest['id']}. {escape(quest['title'])}**: {state}"
        if quest["times"] > 1:
            line += f" (finished {quest['times']} times)"
        if quest.get("saved_at") and quest["kind"] in ("stat", "advancement"):
            line += f", as last saved <t:{int(quest['saved_at'])}:R>"
        lines.append(line)
    return "\n".join(lines)


# -- events and polls (agent/game_events.py) ---------------------------------------------
ATTENDANCE = {
    "advancement": "earn **{goal}** during the event",
    "online": "be online at least 15 minutes during the event",
    "approval": "the owner or a moderator says who came",
}


def event_post(event: dict[str, Any], server: str) -> str:
    """An event's message in the events channel, edited as answers come in."""
    start, end = int(event["starts_at"]), int(event["ends_at"])
    lines = [
        f"**{escape(event['title'])}** on {escape(server)}",
        f"<t:{start}:F> to <t:{end}:t> (<t:{start}:R>)",
    ]
    if event["details"]:
        lines.append(escape(event["details"]))
    goal = short_id(event["goal"]) if event["goal"] else ""
    lines.append("To count as there: " + ATTENDANCE[event["kind"]].format(goal=goal))
    extra = [r for r in event["rewards"] if r["kind"] != "level_xp"]
    xp = ATTEND_XP + sum(r["amount"] for r in event["rewards"] if r["kind"] == "level_xp")
    lines.append(f"For coming: {xp} Level XP" + (f", {rewards(extra)}" if extra else ""))
    if event["start_server"] and event["state"] == "coming":
        lines.append(f"{escape(server)} starts 10 minutes before.")
    answers = event["answers"]
    lines.append(f"Going {answers['going']} · Maybe {answers['maybe']} · Can't {answers['no']}")
    came = sum(a["attended"] for a in event["attendance"])
    lines.append(
        {
            "coming": "",
            "on": "-# On now!",
            "counting": "-# Ended. Counting who came…",
            "approving": "-# Ended. Who came waits for the owner or a moderator.",
            "ended": f"-# Ended. {came} came.",
            "cancelled": "-# Called off.",
        }[event["state"]]
    )
    return "\n".join(line for line in lines if line)


def reminder(event: dict[str, Any], members: list[str]) -> str:
    pings = " ".join(f"<@{m}>" for m in members)
    return f"**{escape(event['title'])}** starts <t:{int(event['starts_at'])}:R>. {pings}"


def poll_post(poll: dict[str, Any], server: str) -> str:
    """A poll's message: how many voted while it's open (so early votes
    don't sway later ones), every answer's count once it's closed."""
    lines = [f"**{escape(poll['question'])}** (poll on {escape(server)})"]
    if poll["open"]:
        lines += [f"{i + 1}. {escape(a)}" for i, a in enumerate(poll["answers"])]
        lines.append(
            f"-# {poll['total']} voted so far. Closes <t:{int(poll['closes_at'])}:R>; "
            "you can change your vote until then."
        )
        return "\n".join(lines)
    for answer, votes in zip(poll["answers"], poll["votes"], strict=True):
        share = round(100 * votes / poll["total"]) if poll["total"] else 0
        lines.append(f"{escape(answer)}: {votes} ({share}%)")
    lines.append(f"-# Closed. {poll['total']} voted.")
    return "\n".join(lines)


def poll_result(poll: dict[str, Any]) -> str:
    question = escape(poll["question"])
    if not poll["total"]:
        return f"The poll **{question}** closed. Nobody voted."
    top = max(poll["votes"])
    won = [escape(a) for a, v in zip(poll["answers"], poll["votes"], strict=True) if v == top]
    if len(won) > 1:
        return f"The poll **{question}** closed: a tie between **" + "** and **".join(won) + "**."
    return f"The poll **{question}** closed: **{won[0]}** won with {top} of {poll['total']} votes."
