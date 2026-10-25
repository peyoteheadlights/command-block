"""The desktop app's Settings tab (Server settings, Game
settings and memory, Version, Bedrock players), driven like a person would
against the real agent with the fake Minecraft servers. Version lists come
from the fake official sources of test_server_types."""

from __future__ import annotations

import httpx
import pytest

from agent import downloads

from .test_desktop_app import (
    button,
    open_server,
    open_window,
    owner,
    shown_text,
    sign_in,
    signed_in_on,
    wait_for,
)
from .test_desktop_pages import core_of, open_page
from .test_server_types import fake_sources

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


def settings_page(qapp, agent_http, name="Survival"):
    window = signed_in_on(qapp, agent_http, name)
    page = open_page(qapp, window, "Settings")
    wait_for(
        qapp, lambda: "Name and color" in shown_text(page) and "Difficulty" in shown_text(page)
    )
    return window, page


def card_of(page, title):
    """The card whose title is ``title``."""
    for frame in page.findChildren(QtWidgets.QFrame):
        heading = frame.findChild(QtWidgets.QLabel, "h2")
        if frame.property("kind") == "card" and heading and heading.text() == title:
            return frame
    raise AssertionError(f"no {title!r} card")


def test_settings_has_four_sections_and_a_jump_to_each(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    for words in ("Server settings", "Game settings", "Kind and version", "Bedrock players"):
        assert button(page, words)
    wait_for(
        qapp,
        lambda: (
            "Let Bedrock players join"
            in [b.text() for b in page.findChildren(QtWidgets.QPushButton)]
        ),
    )


def test_renaming_and_recoloring_a_server_shows_in_its_tab(qapp, agent_http):
    window, page = settings_page(qapp, agent_http)
    identity = card_of(page, "Name and color")
    identity.findChild(QtWidgets.QLineEdit, "serverName").setText("Big Survival")
    [green] = [b for b in identity.findChildren(QtWidgets.QPushButton) if b.toolTip() == "Green"]
    green.click()
    button(identity, "Save name and color").click()
    wait_for(qapp, lambda: window.server_screen.header.name.text() == "Big Survival", 20)
    row = agent_http.get("/api/servers", headers=owner(agent_http)).json()["servers"][0]
    assert (row["name"], row["color"]) == ("Big Survival", "#30C067")


def test_a_card_saves_only_what_changed_and_says_so(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    crashes = card_of(page, "When it crashes")
    button(crashes, "Save").click()
    assert "Nothing changed." in shown_text(crashes)
    crashes.findChild(QtWidgets.QCheckBox).setChecked(True)
    button(crashes, "Save").click()
    headers = owner(agent_http)
    wait_for(
        qapp,
        lambda: agent_http.get("/api/servers/survival/settings", headers=headers).json()["monitor"][
            "auto_restart"
        ],
        20,
    )


def test_a_game_setting_is_saved_and_a_bad_one_is_marked(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    game = card_of(page, "Game settings")
    difficulty = next(c for c in game.findChildren(QtWidgets.QComboBox) if c.findData("hard") >= 0)
    difficulty.setCurrentIndex(difficulty.findData("hard"))
    button(game, "Save").click()
    # Read the file only once it's saved: on Windows, reading it while the agent
    # swaps the new file in makes the agent's save fail.
    wait_for(qapp, lambda: "Saved." in shown_text(page))
    folder = core_of(agent_http).servers["survival"].config.server_dir
    assert "difficulty=hard" in (folder / "server.properties").read_text()
    game = card_of(page, "Game settings")
    port = next(s for s in game.findChildren(QtWidgets.QSpinBox) if s.value() == 25565)
    port.setValue(25566)  # Creative's port
    button(game, "Save").click()
    wait_for(qapp, lambda: "Fix the ones marked" in shown_text(game), 20)
    assert "25566" in shown_text(game)


def test_the_memory_slider_saves_the_limit(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    # The memory card is asked for on its own, so it can come after the rest.
    wait_for(
        qapp, lambda: "Memory" in [h.text() for h in page.findChildren(QtWidgets.QLabel, "h2")]
    )
    memory = card_of(page, "Memory")
    slider = memory.findChild(QtWidgets.QSlider, "memorySlider")
    slider.setValue(slider.value() - 4)  # 2 GB less, in 512 MB steps
    button(memory, "Save memory").click()
    wait_for(qapp, lambda: "Up to 4 GB" in shown_text(page) or "up to 4 GB" in shown_text(page), 20)


def test_changing_the_version_is_checked_first(qapp, agent_http, monkeypatch):
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(fake_sources()))
    _window, page = settings_page(qapp, agent_http)
    wait_for(
        qapp,
        lambda: (
            "Kind and version"
            in [
                f.findChild(QtWidgets.QLabel, "h2").text()
                for f in page.findChildren(QtWidgets.QFrame)
                if f.property("kind") == "card"
            ]
        ),
    )
    version = card_of(page, "Kind and version")
    assert "The version is only known once this server starts" in shown_text(version)
    button(version, "Make the change").click()
    assert not button(version, "Make the change").isEnabled()  # until the list is open
    button(version, "Change the kind or version").click()
    combos = version.findChildren(QtWidgets.QComboBox)
    wait_for(qapp, lambda: combos[1].count() > 0, 20)
    button(version, "Make the change").click()
    assert "Press Check first" in shown_text(version)
    button(version, "Check first").click()
    wait_for(qapp, lambda: "After the change: Fabric" in shown_text(version), 20)


def test_bedrock_players_says_what_the_server_still_needs(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    wait_for(qapp, lambda: "Geyser needs the Fabric API mod" in shown_text(page))
    assert "Port for Bedrock players" in shown_text(page)


def test_a_bedrock_server_has_no_bedrock_players_section(qapp, agent_http):
    window, page = settings_page(qapp, agent_http)
    window.server_screen.row = {**window.server_screen.row, "edition": "bedrock"}
    page.row = window.server_screen.row
    page.load()
    assert not button(page, "Server settings").isHidden()
    assert all(
        b.isHidden()
        for b in page.findChildren(QtWidgets.QPushButton)
        if b.text() == "Bedrock players"
    )


def test_removing_a_server_goes_back_to_all_servers(qapp, agent_http):
    window, page = settings_page(qapp, agent_http, "Creative")
    wait_for(
        qapp,
        lambda: "Remove from list" in [b.text() for b in page.findChildren(QtWidgets.QPushButton)],
    )
    window.confirm = lambda *words: True
    button(card_of(page, "Remove from list"), "Remove from list").click()
    wait_for(
        qapp,
        lambda: window.screen_name == "All servers" and "Creative removed" in shown_text(window),
        20,
    )
    wait_for(qapp, lambda: "Creative" not in [r["name"] for r in window.servers])


def test_a_moderator_sees_settings_they_cant_change_greyed_out(qapp, agent_http):
    made = agent_http.post(
        "/api/accounts",
        json={
            "username": "sam",
            "password": "moderator password",
            "servers": ["survival"],
            "permissions": ["server.view", "settings.game"],
        },
        headers=owner(agent_http),
    )
    assert made.status_code == 200, made.text
    window = open_window(qapp, agent_http)
    sign_in(qapp, window, "sam", "moderator password")
    wait_for(qapp, lambda: window.role == "helper" and "Survival" in shown_text(window))
    open_server(qapp, window, "Survival")
    page = open_page(qapp, window, "Settings")
    wait_for(
        qapp, lambda: "Name and color" in shown_text(page) and "Difficulty" in shown_text(page)
    )
    assert not card_of(page, "When it crashes").isEnabled()
    assert card_of(page, "Game settings").isEnabled()
    assert "Duplicate this server" not in shown_text(page)
