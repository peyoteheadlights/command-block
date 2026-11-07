"""The desktop app's rented servers: Connect a panel, the rows on All
servers marked with the host, and a rented server's screen, against the
real agent and the fake Pterodactyl panel."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from agent import downloads, rented

from .fake_panel import ADDRESS, HOST, KEY, FakePanel
from .test_desktop_app import on_all_servers, open_window, shown_text, sign_in, wait_for
from .test_desktop_backups import asked

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


@pytest.fixture
def panel(monkeypatch):
    found = FakePanel()
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(found.handle))
    monkeypatch.setattr(rented, "CONNECT", found.connect)
    return found


def connected(qapp, agent_http):
    from desktop.rented import ConnectPanel

    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    dialog = ConnectPanel(window)
    dialog.address.setText(ADDRESS)
    dialog.key.setText(KEY)
    dialog.look.click()
    wait_for(qapp, lambda: dialog.problem.isVisibleTo(dialog))
    assert "Tick" in dialog.problem.text()  # the owner's click comes first
    dialog.allow.setChecked(True)
    dialog.look.click()
    wait_for(qapp, lambda: len(dialog.boxes) == 2)
    assert not dialog.warning.isVisibleTo(dialog)  # a sub-user's key
    dialog.boxes["ef567890"].setChecked(True)
    dialog.add.click()
    wait_for(qapp, lambda: "Creative" in shown_text(window) and HOST in shown_text(window))
    return window


def test_connect_a_panel_and_see_the_rented_server_with_its_host(qapp, agent_http, panel):
    window = connected(qapp, agent_http)
    assert "Rented servers" in shown_text(window)
    assert KEY not in shown_text(window)


def test_the_rented_screen_shows_status_console_backups_and_greys_out_the_rest(
    qapp, agent_http, panel
):
    window = connected(qapp, agent_http)
    sid = window.rented[0]["id"]
    window.open_rented(sid)
    screen = window.rented_screen
    assert window.screen_name == "Rented server"
    wait_for(qapp, lambda: "Online" in screen.state.text())
    assert "1.4 GB" in screen.usage.text()
    wait_for(qapp, lambda: "Done" in screen.console.toPlainText())
    wait_for(qapp, lambda: "Before the update" in shown_text(window))
    assert "Your plan keeps up to 3 backups." in shown_text(window)
    assert "Join protection" in shown_text(window)
    assert "come in a later version" in shown_text(window)
    screen.command.setText("say hello")
    screen.send_button.click()
    wait_for(
        qapp, lambda: ("POST", "/servers/ef567890/command", {"command": "say hello"}) in panel.calls
    )
    questions = asked(window, answer=False)
    screen.buttons["kill"].click()
    assert questions and "world can be damaged" in questions[0][1]
    assert not [c for c in panel.calls if c[2] == {"signal": "kill"}]


def test_unknown_status_is_shown_with_the_reason(qapp, agent_http, panel):
    window = connected(qapp, agent_http)
    panel.key = "revoked"
    core = agent_http.agent.app.state.core
    loop = core.rented._task.get_loop()  # the agent's own loop, on its thread
    asyncio.run_coroutine_threadsafe(core.rented.poll_all(), loop).result(10)
    window.open_rented(window.rented[0]["id"])
    screen = window.rented_screen
    wait_for(qapp, lambda: "refused the key" in screen.state.text())
    assert screen.state.text().startswith("Unknown")
    assert not screen.buttons["stop"].isEnabled()
