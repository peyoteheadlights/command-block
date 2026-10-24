"""Levels: the Discord bot's progression for each Discord member.

XP only comes from things Command Block measures (Mark's rule): 10 per hour
played, worked out from our own player sessions and added up over every
Minecraft account the member linked, on every server; and what the ledger
holds for events attended and quests finished (``award``). Nothing for
Discord messages, so nobody can farm it.

Level n needs 8 x n x n XP: Level 5 after 20 hours played, 10 after 80, 25
after 500. Which Discord role goes with which Level is the bot's side
(agent/discord/layout.py); this module knows nothing about Discord.
"""

from __future__ import annotations

import math
import time
from typing import Any

XP_PER_HOUR = 10
STEP = 8
TOP = 10  # rows on a leaderboard


def level_of(xp: int) -> int:
    return math.isqrt(max(xp, 0) // STEP)


def xp_for(level: int) -> int:
    """The XP a Level starts at."""
    return STEP * level * level


def all_xp(db) -> dict[str, int]:
    """Discord member id -> XP, for everyone with a linked account or XP."""
    rows = db.query(
        "SELECT l.discord_id AS member, SUM(p.total_seconds + CASE WHEN p.online = 1 AND "
        "p.session_started IS NOT NULL THEN MAX(? - p.session_started, 0) ELSE 0 END) AS seconds "
        "FROM discord_links l JOIN players p ON p.uuid = l.uuid GROUP BY l.discord_id",
        (time.time(),),
    )
    xp = {r["member"]: int(float(r["seconds"] or 0) * XP_PER_HOUR // 3600) for r in rows}
    for r in db.query("SELECT discord_id, SUM(amount) AS total FROM level_xp GROUP BY discord_id"):
        xp[r["discord_id"]] = xp.get(r["discord_id"], 0) + int(r["total"] or 0)
    return xp


def award(db, discord_id: str, amount: int, source: str, ref: str) -> bool:
    """XP for an event attended or a quest finished. Once per (source, ref),
    so a repeat call gives nothing; False when it was given already."""
    done = db.execute(
        "INSERT OR IGNORE INTO level_xp (discord_id, source, ref, amount, at) "
        "VALUES (?, ?, ?, ?, ?)",
        (discord_id, source, ref, int(amount), time.time()),
    )
    return bool(done.rowcount)


def standing(xp: int) -> dict[str, int]:
    level = level_of(xp)
    return {"level": level, "xp": xp, "next_level_xp": xp_for(level + 1)}


def weekly_playtime(db, server_ids: list[str], until: float | None = None) -> list[dict[str, Any]]:
    """The most played in the 7 days before ``until`` on these servers, from
    our own player sessions (a session still going counts up to now)."""
    until = until or time.time()
    since = until - 7 * 86400
    if not server_ids:
        return []
    marks = ",".join("?" * len(server_ids))
    rows = db.query(
        "SELECT username, SUM(MIN(COALESCE(left_at, ?), ?) - MAX(joined_at, ?)) AS seconds "
        f"FROM player_sessions WHERE server_id IN ({marks}) AND joined_at < ? "
        "AND COALESCE(left_at, ?) > ? GROUP BY username ORDER BY seconds DESC LIMIT ?",
        (until, until, since, *server_ids, until, until, since, TOP),
    )
    return [{"name": r["username"], "seconds": float(r["seconds"])} for r in rows if r["seconds"]]


def top_levels(db, server_ids: list[str]) -> list[tuple[str, int]]:
    """The members with the most XP among those whose linked accounts played
    on these servers: (member id, XP)."""
    if not server_ids:
        return []
    marks = ",".join("?" * len(server_ids))
    members = {
        r["discord_id"]
        for r in db.query(
            "SELECT DISTINCT l.discord_id FROM discord_links l JOIN players p ON p.uuid = l.uuid "
            f"WHERE p.server_id IN ({marks})",
            tuple(server_ids),
        )
    }
    xp = all_xp(db)
    return sorted(((m, xp.get(m, 0)) for m in members), key=lambda x: (-x[1], x[0]))[:TOP]
