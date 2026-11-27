"""The new look of the Overview, Settings and All servers: the header's
status mark and icon buttons, a server's box in its own color, the card to
add a server, a switch that saves by itself, and graphs that fit a narrow
window. Driven against the real agent with the fake Minecraft servers."""

from __future__ import annotations

import pytest

from desktop.agent import AgentError
from desktop.parts import IconButton, Status, Switch
from desktop.window import ServerRow

from .test_desktop_app import (
    button,
    on_all_servers,
    open_window,
    shown_text,
    sign_in,
    signed_in_on,
    technical_on,
    wait_for,
)
from .test_desktop_settings import card_of, settings_page

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


def test_the_header_has_one_status_mark_and_icon_buttons_with_names(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    header = window.server_screen.header
    [status] = header.findChildren(Status)
    assert (status.state, status.text()) == ("off", "Offline")
    # No pills: what else is known is one plain line next to the mark.
    assert not [w for w in header.findChildren(QtWidgets.QLabel) if w.property("kind") == "chip"]
    assert "Fabric" in header.facts.text()
    assert isinstance(header.diagnose, IconButton)
    assert header.diagnose.accessibleName() == "Diagnose server"
    assert header.diagnose.property("hoverName") == "Diagnose server"
    # Start is the screen's one filled button; Stop is not filled.
    assert header.main.property("kind") == "primary"
    header.show_row({**header.row, "state": "ONLINE"})
    assert (status.state, status.text()) == ("on", "Online")
    assert header.main.text() == "Stop" and header.main.property("kind") == ""
    assert header.restart.accessibleName() == "Restart"
    header.show_row({**header.row, "state": "ONLINE", "answering": {"ok": False}})
    assert status.state == "bad"
    header.show_row({**header.row, "state": "STARTING"})
    assert status.state == "wait"


def test_each_server_on_all_servers_is_a_box_in_its_own_color(qapp, agent_http):
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    screen = window.screens["All servers"]
    rows = screen.findChildren(ServerRow)
    assert len(rows) == 2
    colors = [r["color"] for r in window.servers]
    for row, color in zip(rows, colors, strict=True):
        assert row.look["accent"].lower() == color.lower()
        assert row.look["accent"] in row.styleSheet()  # its Start button is filled with it
        [status] = row.findChildren(Status)
        assert status.text() == "Offline"
    assert rows[0].look["border-strong"] != rows[1].look["border-strong"]
    # One group only (this PC's servers): no heading above it.
    assert not [w for w in screen.findChildren(QtWidgets.QLabel) if w.property("kind") == "group"]
    button(screen, "Add server").click()
    assert window.screen_name == "New server"


def test_with_no_server_all_servers_is_one_filled_add_button(qapp, agent_http):
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    screen = window.screens["All servers"]
    assert button(screen, "Add server").property("kind") == "plain"
    # Let the loads from signing in finish, and stop the agent's events from
    # loading the list again, or they fill it back with the two servers.
    wait_for(qapp, lambda: not window._tasks and not window.soon.isActive())
    window.feed.message.disconnect(window.feed_message)
    screen.fill([])
    wait_for(qapp, lambda: "No servers yet" in shown_text(screen))
    add = button(screen, "Add server")
    assert add.property("kind") == "primary" and add.property("big")
    assert button(screen, "Connect a rented server")
    add.click()
    assert window.screen_name == "New server"


def test_a_switch_the_agent_refuses_goes_back_and_says_why(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    crashes = card_of(page, "Restart after a crash")
    switch = crashes.findChild(Switch)
    assert not switch.isChecked()
    asked = []

    def refuse(method, path, done, failed=None, body=None, **_more):
        asked.append((method, path, body))
        failed(AgentError("Not allowed right now."))

    page.ask = refuse
    switch.click()
    assert asked == [("PUT", "/settings", {"updates": {"monitor.auto_restart": True}})]
    assert not switch.isChecked()  # never left on something that was not saved
    assert "Not allowed right now." in shown_text(crashes)


def test_the_technical_overview_fits_a_narrow_window(qapp, agent_http):
    window = technical_on(qapp, agent_http)
    overview = window.server_screen.technical_overview
    scroll = window.server_screen.page_widgets["Overview"]
    wait_for(qapp, lambda: overview.columns == 4)
    tops = {box.y() for box in overview.graph_boxes}
    assert len(tops) == 1  # four in a row
    window.resize(900, 760)
    wait_for(qapp, lambda: overview.columns == 2)
    qapp.processEvents()
    assert len({box.y() for box in overview.graph_boxes}) == 2  # two by two
    assert scroll.horizontalScrollBar().maximum() == 0  # nothing to scroll sideways
