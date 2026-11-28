"""The Companion's join checks, read from the server's console: each
player's modpack version for the Players page, failed checks in Recent
activity, and an alert after 3 failed checks in a day from one player.

The Companion writes one line per check (Fabric, NeoForge and Paper each
put their own prefix in front):

    Companion check: Steve passed, modpack 1.4
    Companion check: Alex failed, modpack -
"""

from __future__ import annotations

import pytest

from agent.database.db import Database
from agent.events import EventBus
from agent.minecraft.console import extract_signals, parse_line
from agent.monitoring.players import PlayerTracker
from agent.notifications.dispatcher import EVENT_MAP


def signals(raw: str):
    return extract_signals(parse_line(raw, 1))


@pytest.mark.parametrize(
    "raw",
    [
        "[12:00:00] [Server thread/INFO] (Companion) Companion check: Steve passed, modpack 1.4",
        "[12:00:00] [Server thread/INFO] [Companion/]: Companion check: Steve passed, modpack 1.4",
        "[12:00:00 INFO]: [CommandBlockCompanion] Companion check: Steve passed, modpack 1.4",
    ],
)
def test_each_loader_s_check_line_is_read(raw):
    assert signals(raw).companion_check == ("Steve", True, "1.4")


def test_a_failed_check_without_a_version():
    raw = "[12:00:00] [Server thread/INFO] (Companion) Companion check: Alex failed, modpack -"
    assert signals(raw).companion_check == ("Alex", False, None)


@pytest.mark.parametrize(
    "raw",
    [
        # A player can't fake one in chat.
        "[12:00:00] [Server thread/INFO] (Minecraft) <Bob> Companion check: Steve failed, modpack -",
        "[12:00:00] [Server thread/INFO] (Minecraft) [Not Secure] <Bob> Companion check: Steve failed, modpack -",
        "[12:00:00] [Server thread/INFO] (Companion) Companion check: Steve maybe, modpack 1",
        "[12:00:00] [Server thread/INFO] (Companion) Companion check: Steve passed, modpack a b",
        # Bedrock players (Floodgate) aren't checked, so they never count as failed.
        "[12:00:00] [Server thread/INFO] (Companion) Companion check: .Steve skipped, Bedrock player",
    ],
)
def test_lines_that_only_look_like_a_check_are_ignored(raw):
    assert signals(raw).companion_check is None


@pytest.fixture
def tracker(config):
    bus = EventBus()
    db = Database(config.database_path)
    db.register_server("test", "Test", str(config.server_dir))
    seen = []

    async def keep(event):
        seen.append(event)

    bus.subscribe(keep)
    return PlayerTracker(config, bus, db, "test"), seen


async def test_the_version_shows_on_the_players_page_from_the_first_join(tracker):
    tracker, _ = tracker
    # The check comes before "joined the game", so a new player isn't a row yet.
    await tracker.companion_checked("Steve", True, "1.4")
    await tracker.player_joined("Steve")
    row = tracker.all_players()[0]
    assert row["modpack_version"] == "1.4" and row["modpack_ok"] == 1

    await tracker.companion_checked("Steve", False, "1.3")
    row = tracker.all_players()[0]
    assert row["modpack_version"] == "1.3" and row["modpack_ok"] == 0


async def test_failed_checks_reach_recent_activity_and_the_third_in_a_day_alerts(tracker):
    tracker, seen = tracker
    await tracker.companion_checked("Steve", True, "1.4")
    assert not seen, "a passed check isn't news"
    for _ in range(4):
        await tracker.companion_checked("Alex", False, None)
    failed = [e for e in seen if e.type == "companion_check_failed"]
    assert len(failed) == 4 and "Alex" in failed[0].message
    alerts = [e for e in seen if e.type == "companion_checks_failing"]
    assert len(alerts) == 1, "one alert, at the third failure"
    assert alerts[0].data["username"] == "Alex"
    assert "companion_checks_failing" in EVENT_MAP
    assert "companion_check_failed" not in EVENT_MAP, "each failure alone isn't an alert"


def test_the_players_page_says_which_modpack_each_player_has():
    from desktop.players import modpack_words

    assert modpack_words({}) is None, "never checked: nothing shown"
    assert modpack_words({"modpack_ok": 1, "modpack_version": "1.4"}) == "Modpack 1.4"
    assert "not the right one" in modpack_words({"modpack_ok": 0, "modpack_version": "1.3"})
    assert modpack_words({"modpack_ok": 0, "modpack_version": ""}) == "No modpack"
