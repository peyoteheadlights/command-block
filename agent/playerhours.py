"""When people play: the busiest player count of each hour, kept 28 days.

The metrics loop (agent/monitoring/metrics.py) passes each measured count
in. One row per hour is written when the hour starts and again only when
the count goes up, so this costs almost nothing. An hour the agent wasn't
running, or couldn't measure (the server running but not yet asked who is
on), has no row and is shown as Unknown, never as 0.
"""

from __future__ import annotations

import time
from typing import Any

DAYS = 28


class HourPeak:
    """The busiest count of the current hour, for one server."""

    def __init__(self, db, server_id: str):
        self.db = db
        self.server_id = server_id
        self.hour = -1
        self.peak = -1

    def note(self, count: int, now: float | None = None) -> None:
        hour = int((now or time.time()) // 3600)
        if hour == self.hour and count <= self.peak:
            return
        if hour != self.hour:
            self.hour, self.peak = hour, -1
        self.peak = count
        self.db.execute(
            "INSERT INTO player_hours (server_id, hour, players) VALUES (?, ?, ?) "
            "ON CONFLICT(server_id, hour) DO UPDATE SET players = MAX(players, excluded.players)",
            (self.server_id, hour, count),
        )


def profile(db, server_id: str, now: float | None = None) -> dict[str, Any]:
    """Each hour of the day (this PC's time) over the last 28 days: the
    average of the busiest counts measured, or None when never measured,
    and the quietest measured hour."""
    now_hour = int((now or time.time()) // 3600)
    rows = db.query(
        "SELECT hour, players FROM player_hours WHERE server_id = ? AND hour > ? AND hour <= ?",
        (server_id, now_hour - DAYS * 24, now_hour),
    )
    seen: list[list[int]] = [[] for _ in range(24)]
    for row in rows:
        seen[time.localtime(row["hour"] * 3600).tm_hour].append(row["players"])
    hours = [
        {
            "hour": hour,
            "average": round(sum(counts) / len(counts), 1) if counts else None,
            "busiest": max(counts) if counts else None,
            "measured_days": len(counts),
        }
        for hour, counts in enumerate(seen)
    ]
    known = [h for h in hours if h["average"] is not None]
    quietest = min(known, key=lambda h: (h["average"], h["hour"]))["hour"] if known else None
    return {
        "days": DAYS,
        "hours": hours,
        "quietest_hour": quietest,
        "measured_hours": len(rows),
        "total_hours": DAYS * 24,
    }
