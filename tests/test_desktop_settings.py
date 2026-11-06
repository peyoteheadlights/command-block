"""The desktop app's Settings tab (Server settings, Game
settings and memory, Version, Bedrock players), driven like a person would
against the real agent with the fake Minecraft servers. Version lists come
from the fake official sources of test_server_types."""

from __future__ import annotations

import httpx
import pytest

from agent import downloads
from desktop.parts import InfoDot, Switch

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
    wait_for(qapp, lambda: "Name and color" in shown_text(page) and loaded(page, "Difficulty"))
    return window, page


def loaded(page, words) -> bool:
    """``words`` is on the page, in the section that shows or in another one."""
    return any(words in w.text() for w in page.findChildren(QtWidgets.QLabel))


def section(page, words):
    """Clicks one of the four sections at the top of Settings."""
    button(page, words).click()
    return page


def switch_named(frame, words):
    [switch] = [s for s in frame.findChildren(Switch) if s.accessibleName() == words]
    return switch


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
    # No Save button: the name saves on Enter, the color on its click.
    assert not [b for b in identity.findChildren(QtWidgets.QPushButton) if "Save" in b.text()]
    name = identity.findChild(QtWidgets.QLineEdit, "serverName")
    name.setText("Big Survival")
    name.editingFinished.emit()
    wait_for(qapp, lambda: window.server_screen.header.name.text() == "Big Survival", 20)
    identity = card_of(page, "Name and color")
    [green] = [b for b in identity.findChildren(QtWidgets.QPushButton) if b.toolTip() == "Green"]
    green.click()
    wait_for(qapp, lambda: window.server_screen.row.get("color") == "#30C067", 20)
    wait_for(qapp, lambda: window.server_screen.header.name.text() == "Big Survival", 20)
    row = agent_http.get("/api/servers", headers=owner(agent_http)).json()["servers"][0]
    assert (row["name"], row["color"]) == ("Big Survival", "#30C067")


def test_a_card_saves_only_what_changed_and_says_so(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    crashes = card_of(page, "Restart after a crash")
    [dot] = crashes.findChildren(InfoDot)  # the sentence that explains the switch
    assert dot.accessibleDescription().startswith("If the server crashes")
    button(crashes, "Save").click()
    assert "Nothing changed." in shown_text(crashes)
    crashes.findChild(Switch).click()  # a switch saves by itself, with no Save
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
    game = card_of(section(page, "Game settings"), "Game settings")
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
    memory = card_of(section(page, "Game settings"), "Memory")
    slider = memory.findChild(QtWidgets.QSlider, "memorySlider")
    slider.setValue(slider.value() - 4)  # 2 GB less, in 512 MB steps
    # No Save button: it saves a moment after the slider stops moving.
    assert not [b for b in memory.findChildren(QtWidgets.QPushButton) if "Save" in b.text()]
    headers = owner(agent_http)
    wait_for(
        qapp,
        lambda: (
            agent_http.get("/api/servers/survival/memory", headers=headers).json()["limit_mb"]
            == 4096
        ),
        20,
        pause=0.3,
    )
    # The page is read again from the agent: the big number is what was saved.
    wait_for(
        qapp,
        lambda: (
            "Saved." in shown_text(page)
            and card_of(page, "Memory").findChild(QtWidgets.QLineEdit, "bigNumber").text() == "4"
        ),
    )


def test_the_big_number_on_a_slider_is_a_field_and_view_distance_saves(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    view = card_of(section(page, "Game settings"), "View distance")
    slider = view.findChild(QtWidgets.QSlider, "viewSlider")
    big = view.findChild(QtWidgets.QLineEdit, "bigNumber")
    assert big.text() == str(slider.value())
    big.setText("7")
    big.editingFinished.emit()  # Enter
    assert slider.value() == 7
    folder = core_of(agent_http).servers["survival"].config.server_dir
    wait_for(qapp, lambda: "Saved." in shown_text(page))
    assert "view-distance=7" in (folder / "server.properties").read_text()
    # The form under it no longer has a second View distance field.
    game = card_of(page, "Game settings")
    assert "View distance" not in shown_text(game)


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
    version = card_of(section(page, "Kind and version"), "Kind and version")
    assert "The version is only known once this server starts" in shown_text(version)
    button(version, "Make the change").click()
    assert not button(version, "Make the change").isEnabled()  # until the list is open
    button(version, "Change the version").click()
    combos = version.findChildren(QtWidgets.QComboBox)
    wait_for(qapp, lambda: combos[0].count() > 0, 20)
    button(version, "Make the change").click()
    assert "Press Check first" in shown_text(version)
    button(version, "Check first").click()
    wait_for(qapp, lambda: "After the change: Fabric" in shown_text(version), 20)

    # A check that answers after another version was picked is an old answer:
    # it never lets the change go ahead for the new pick.
    late = []
    monkeypatch.setattr(page, "ask", lambda *args, **kw: late.append(args[2]))
    combos[0].setCurrentIndex(0)
    button(version, "Check first").click()
    assert combos[0].count() > 1
    combos[0].setCurrentIndex(1)
    late[0]({"target": {"type_name": "Fabric", "minecraft_version": "old"}})
    button(version, "Make the change").click()
    assert "Press Check first" in shown_text(version)
    assert len(late) == 1  # nothing was sent


def test_change_server_type_is_its_own_card_and_never_guesses_the_version(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    section(page, "Kind and version")
    wait_for(qapp, lambda: "Start the server once" in shown_text(page), 20)
    card = card_of(page, "Change server type")
    assert "Your world, settings and Minecraft version stay" in shown_text(card)
    assert not [
        b for b in card.findChildren(QtWidgets.QPushButton) if b.text() == "Stop and change"
    ]


def test_bedrock_players_says_what_the_server_still_needs(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    wait_for(qapp, lambda: loaded(page, "Geyser needs the Fabric API mod"))
    section(page, "Bedrock players")
    assert "Geyser needs the Fabric API mod" in shown_text(page)
    assert "Port for Bedrock players" in shown_text(page)


def test_a_bedrock_server_has_no_bedrock_players_section(qapp, agent_http):
    window, page = settings_page(qapp, agent_http)
    # The fake server is Java. Every refresh of the server list shows the
    # agent's row again (and rebuilds the pages when it changes), so keep
    # this server Bedrock for the window's whole life, then open Settings anew.
    shown = window.server_screen.show_row
    window.server_screen.show_row = lambda row: shown({**row, "edition": "bedrock"})
    window.server_screen.show_row(window.server_screen.row)
    page = open_page(qapp, window, "Settings")
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
    wait_for(qapp, lambda: "Name and color" in shown_text(page) and loaded(page, "Difficulty"))
    assert not card_of(page, "Restart after a crash").isEnabled()
    assert card_of(page, "Game settings").isEnabled()
    assert "Duplicate this server" not in shown_text(page)


def test_update_bedrock_by_itself_is_an_owner_switch_off_by_default(qapp, agent_http):
    window, page = settings_page(qapp, agent_http)
    # The fake server is Java. Every refresh of the server list shows the
    # agent's row again (and rebuilds the pages when it changes), so keep
    # this server Bedrock for the window's whole life, then open Settings anew.
    shown = window.server_screen.show_row
    window.server_screen.show_row = lambda row: shown({**row, "edition": "bedrock"})
    window.server_screen.show_row(window.server_screen.row)
    page = open_page(qapp, window, "Settings")
    words = "Update Bedrock by itself"
    wait_for(qapp, lambda: words in [b.accessibleName() for b in page.findChildren(Switch)])
    frame = card_of(page, "Bedrock updates")
    switch = switch_named(frame, words)
    assert not switch.isChecked()
    switch.click()
    core = core_of(agent_http)
    wait_for(qapp, lambda: core.servers["survival"].config.server.bedrock_auto_update)


def test_the_version_picker_says_how_old_a_saved_list_is(qapp, agent_http, monkeypatch):
    from .test_keeping_current import sources

    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(sources()))
    agent_http.get("/api/server-types/fabric/versions", headers=owner(agent_http))  # saved
    monkeypatch.setattr(
        downloads, "TRANSPORT", httpx.MockTransport(sources(down=("meta.fabricmc.net",)))
    )
    _window, page = settings_page(qapp, agent_http)
    wait_for(
        qapp,
        lambda: (
            "Change the version" in [b.text() for b in page.findChildren(QtWidgets.QPushButton)]
        ),
    )
    version = card_of(section(page, "Kind and version"), "Kind and version")
    button(version, "Change the version").click()
    wait_for(qapp, lambda: "ago, couldn't reach Fabric." in shown_text(version), 20)


def test_join_protection_is_one_switch_and_saves(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    join = card_of(page, "Join protection")
    # The fake servers run from a start command, which can't take --port.
    assert "started with its own start command" in shown_text(join)
    assert "Blocked addresses" not in shown_text(join)  # Technical only
    switch_named(join, "Turn on Join protection").click()
    headers = owner(agent_http)
    wait_for(
        qapp,
        lambda: agent_http.get("/api/servers/survival/settings", headers=headers).json()["server"][
            "join_protection"
        ],
        20,
    )
