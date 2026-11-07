"""The desktop app's parts of the use-case extras: the players-by-hour graph
and the quietest hour, command buttons on the Console page, New season on
the Backups page, and Test servers hidden in Simple mode."""

from __future__ import annotations

import time

import pytest

from agent import playerhours

from .test_desktop_app import button, open_server, open_window, shown_text, sign_in, wait_for
from .test_desktop_backups import asked, backups_page
from .test_desktop_pages import buttons_named, open_page, running, technical_page

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


def test_the_players_page_draws_hours_and_unknown_hours(qapp, agent_http):
    core = agent_http.agent.app.state.core
    playerhours.HourPeak(core.db, "survival").note(4)
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    wait_for(qapp, lambda: "Survival" in shown_text(window))
    open_server(qapp, window, "Survival")
    page = open_page(qapp, window, "Players and chat")
    wait_for(qapp, lambda: "Quietest" in page.hours_line.text())
    graph = page.hours_graph
    known = [h for h in graph.hours if h["average"] is not None]
    assert len(graph.hours) == 24 and known and known[0]["busiest"] == 4
    assert "Unknown" in graph.accessibleDescription()
    graph.resize(400, 160)
    graph.grab()  # paints without errors


def test_the_schedule_form_offers_the_quietest_hour(qapp, agent_http):
    core = agent_http.agent.app.state.core
    playerhours.HourPeak(core.db, "survival").note(0)
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    wait_for(qapp, lambda: "Survival" in shown_text(window))
    open_server(qapp, window, "Survival")
    page = open_page(qapp, window, "Schedules")
    wait_for(qapp, lambda: page.quiet_use.isVisibleTo(page))
    page.expr.setText("sun 04:00")
    page.quiet_use.click()
    assert page.expr.text() == f"sun {time.localtime().tm_hour:02d}:00"


def test_command_buttons_are_made_and_pressed_on_the_console(qapp, agent_http):
    running(qapp, agent_http)
    window, page = technical_page(qapp, agent_http, "Console")
    # The Edit button is made when the buttons arrive from the agent.
    wait_for(qapp, lambda: hasattr(page, "edit_button") and page.edit_button.isVisibleTo(page))
    page.edit_button.click()
    page.editor.setPlainText("Hello: say hi from a button\n")
    page.edit_button.click()
    wait_for(qapp, lambda: [b["name"] for b in page.buttons] == ["Hello"])
    wait_for(qapp, lambda: buttons_named(page, "Hello"))
    button(page, "Hello").click()
    wait_for(qapp, lambda: "hi from a button" in page.log.toPlainText(), 20)


def test_new_season_asks_first_and_keeps_the_old_world(qapp, agent_http):
    window, page = backups_page(qapp, agent_http)
    wait_for(qapp, lambda: page.season_button.isVisibleTo(page))
    questions = asked(window)
    page.announce.setChecked(False)
    page.season_button.click()
    assert questions and "new season" in questions[0][0].lower()
    wait_for(qapp, lambda: "new season is ready" in page.note.text(), 60)
    wait_for(qapp, lambda: "Old season, kept forever" in shown_text(page))


def test_simple_mode_hides_test_servers(qapp, agent_http):
    core = agent_http.agent.app.state.core
    core.servers["creative"].config.set("server.test", True)
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    wait_for(qapp, lambda: "Survival" in shown_text(window))
    assert "Creative" not in shown_text(window.screens["All servers"])
    window.set_mode(True)
    wait_for(qapp, lambda: "Creative" in shown_text(window.screens["All servers"]))
    assert "Test" in shown_text(window.screens["All servers"])
