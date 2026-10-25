"""Events and polls on the Quests and events page, clicked through against
the real agent with the fake Discord and the fake Minecraft servers."""

from __future__ import annotations

import asyncio
import time

import pytest

from .fake_discord import MEMBER_ID, wait_for
from .test_desktop_app import button, shown_text
from .test_desktop_app import wait_for as wait_for_window
from .test_desktop_discord import discord_http  # noqa: F401  (the fixture)
from .test_desktop_quests import ALEX, named, quests_page  # noqa: F401  (a fixture)
from .test_discord import fake  # noqa: F401  (the fixture)

QtCore = pytest.importorskip("PySide6.QtCore", reason="PySide6 is in requirements-dev")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")

HOUR = 3600


def ended_event(window, core, kind="online"):
    """An event made through the app, moved into the past, with Alex
    (Sam's linked account) online for 20 minutes of it, then counted."""
    core.links.link(ALEX, "Alex", "java", "survival", MEMBER_ID, "sam", "test")
    soon = time.time() + HOUR
    made = window.agent.on(
        "survival",
        "POST",
        "/quests/events",
        {"title": "Build night", "kind": kind, "starts_at": soon, "ends_at": soon + HOUR},
    )
    event_id = made["events"][0]["id"]
    start = time.time() - 2 * HOUR
    core.db.execute(
        "UPDATE game_events SET starts_at = ?, ends_at = ? WHERE id = ?",
        (start, start + HOUR, event_id),
    )
    core.db.execute(
        "INSERT INTO players (server_id, username, uuid, first_seen, last_seen, total_seconds, "
        "sessions) VALUES ('survival', 'Alex', ?, 1, 1, 0, 1)",
        (ALEX,),
    )
    core.db.execute(
        "INSERT INTO player_sessions (server_id, username, joined_at, left_at) VALUES "
        "('survival', 'Alex', ?, ?)",
        (start, start + 1200),
    )
    return event_id


def count(core):
    """The events check, now, in the agent's own loop (it runs every 30 s)."""
    loop = core.game_events._task.get_loop()
    asyncio.run_coroutine_threadsafe(core.game_events.check(), loop).result(10)


def test_an_event_is_made_from_the_form_and_called_off(qapp, quests_page):  # noqa: F811
    window, page, core = quests_page
    wait_for_window(qapp, lambda: "No events yet" in shown_text(page))
    named(page, QtWidgets.QLineEdit, "Title, New event").setText("Dragon night")
    kind = named(page, QtWidgets.QComboBox, "Who counts as there, New event")
    kind.setCurrentIndex(kind.findData("advancement"))
    goal = named(page, QtWidgets.QLineEdit, "Which advancement, New event")
    assert goal.isEnabled()
    goal.setText("end/kill_dragon")
    starts = named(page, QtWidgets.QDateTimeEdit, "Starts, New event")
    ends = named(page, QtWidgets.QDateTimeEdit, "Ends, New event")
    # Ends before it starts: the agent's words show in the form.
    ends.setDateTime(starts.dateTime().addSecs(-600))
    button(page, "Make event").click()
    wait_for_window(qapp, lambda: "15 minutes to 24 hours" in shown_text(page))
    ends.setDateTime(starts.dateTime().addSecs(2 * HOUR))
    next(
        b for b in page.findChildren(QtWidgets.QCheckBox) if b.text().startswith("Start the server")
    ).setChecked(True)
    named(page, QtWidgets.QSpinBox, "Level XP, New event").setValue(25)
    button(page, "Make event").click()
    wait_for_window(qapp, lambda: "Dragon night" in shown_text(page))
    words = shown_text(page)
    assert "Counts if end/kill dragon is earned during it" in words
    assert "50 Level XP, 25 Level XP" in words and "starts the server 10 minutes before" in words
    assert "Going 0 · Maybe 0 · Can't 0" in words
    assert "15 minutes to 24 hours" not in words  # the problem is gone
    made = core.game_events.recent("survival")[0]
    assert made["ends_at"] - made["starts_at"] == 2 * HOUR and made["start_server"] == 1
    asked = []
    window.confirm = lambda title, body, yes: asked.append(title) or True
    button(page, "Call off").click()
    wait_for_window(qapp, lambda: "Called off" in shown_text(page))
    assert asked == ["Call off Dragon night?"]


def test_an_approval_list_is_ticked_and_approved(qapp, quests_page):  # noqa: F811
    window, page, core = quests_page
    event_id = ended_event(window, core, kind="approval")
    count(core)
    page.load()
    wait_for_window(qapp, lambda: "Who came waits for your OK" in shown_text(page))
    tick = named(page, QtWidgets.QCheckBox, "Came: sam")
    assert tick.isChecked() and tick.isEnabled() and "sam (20 min)" in tick.text()
    button(page, "Approve list").click()
    wait_for_window(qapp, lambda: "Approve list" not in [b.text() for b in _buttons(page)])
    assert core.game_events.get("survival", event_id)["approved_at"]
    from agent import levels

    assert levels.all_xp(core.db)[MEMBER_ID] == 50
    # Unticked after the OK: the Level XP goes back, logged.
    named(page, QtWidgets.QCheckBox, "Came: sam").setChecked(False)
    wait_for(lambda: levels.all_xp(core.db).get(MEMBER_ID, 0) == 0)
    assert core.db.query("SELECT 1 FROM audit_log WHERE action = 'event_attendance_fix'")


def _buttons(page):
    return [b for b in page.findChildren(QtWidgets.QPushButton) if b.isVisibleTo(page)]


def test_a_poll_is_made_and_closed_early(qapp, quests_page):  # noqa: F811
    window, page, core = quests_page
    wait_for_window(qapp, lambda: "No polls yet" in shown_text(page))
    named(page, QtWidgets.QLineEdit, "Question, New poll").setText("Next map?")
    named(page, QtWidgets.QLineEdit, "Answer 1").setText("Desert")
    button(page, "Make poll").click()
    wait_for_window(qapp, lambda: "2 to 5 answers" in shown_text(page))
    named(page, QtWidgets.QLineEdit, "Answer 2").setText("Jungle")
    button(page, "Make poll").click()
    wait_for_window(qapp, lambda: "Desert: 0  ·  Jungle: 0" in shown_text(page))
    assert named(page, QtWidgets.QLineEdit, "Question, New poll").text() == ""
    button(page, "Close now").click()
    wait_for_window(qapp, lambda: "Closed. 0 voted." in shown_text(page))
    assert core.game_events.polls("survival")[0]["closed_at"]


def test_events_paint_small_and_large_in_both_modes(qapp, quests_page):  # noqa: F811
    window, page, core = quests_page
    ended_event(window, core)
    count(core)
    for technical, size in ((False, (760, 520)), (True, (1600, 1000))):
        window.set_mode(technical)
        window.resize(*size)
        page.load()
        # Each mode's own word, so this waits for the reload in that mode.
        ended = "ended" if technical else "Ended"
        wait_for_window(qapp, lambda ended=ended: ended in shown_text(page))
        qapp.processEvents()
        page.grab()
    assert "measured" in shown_text(page) or any(
        "measured" in b.text() for b in page.findChildren(QtWidgets.QCheckBox)
    )
    window.set_mode(False)
