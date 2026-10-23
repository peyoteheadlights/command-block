"""The desktop app's "+" tab: a new server (kind table, version,
name, folder, rules), a server already on this PC, and a modpack read first,
against the real agent with the fake official version sources."""

from __future__ import annotations

import httpx
import pytest

from agent import downloads
from agent.servertypes import create as create_module

from .conftest import make_server_folder
from .fake_pc import FakePC
from .test_desktop_app import (
    button,
    on_all_servers,
    open_window,
    owner,
    shown_text,
    sign_in,
    wait_for,
)
from .test_server_types import MC, fake_sources

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


def plus_tab(qapp, agent_http, pc=None):
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    binder = window.findChild(QtWidgets.QTabBar, "binder")
    binder.setCurrentIndex(binder.count() - 1)
    screen = window.new_server
    wait_for(
        qapp,
        lambda: (
            window.screen_name == "New server" and "Which kind of server?" in shown_text(screen)
        ),
    )
    return window, screen


def test_a_new_server_is_made_after_the_rules_are_ticked_and_opens(
    qapp, agent_http, monkeypatch, tmp_path
):
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(fake_sources()))
    monkeypatch.setattr(create_module, "java_problem", lambda *args, **kwargs: None)
    window, screen = plus_tab(qapp, agent_http)
    assert "Recommended" in shown_text(screen)
    screen.choose_buttons["vanilla"].click()
    wait_for(qapp, lambda: screen.findChild(QtWidgets.QComboBox, "newVersion") is not None, 20)
    wait_for(qapp, lambda: screen.choice["minecraft"] == MC, 20)
    screen.findChild(QtWidgets.QLineEdit, "newName").setText("Fresh World")
    folder = tmp_path / "fresh"
    screen.folder.setText(str(folder))
    button(screen, "Create server").click()
    assert "Tick the box to agree to Minecraft's rules." in shown_text(screen)
    assert not folder.exists()
    screen.findChild(QtWidgets.QCheckBox, "newEula").setChecked(True)
    button(screen, "Create server").click()
    wait_for(
        qapp,
        lambda: window.screen_name == "Server" and "Fresh World is ready" in shown_text(window),
        30,
    )
    assert (folder / "server.jar").is_file()


def test_the_rules_links_open_in_the_browser(qapp, agent_http, monkeypatch):
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(fake_sources()))
    pc = FakePC()
    _window, screen = plus_tab(qapp, agent_http, pc)
    screen.choose_buttons["vanilla"].click()
    wait_for(qapp, lambda: "Minecraft's rules" in shown_text(screen), 20)
    button(screen, "Read Minecraft's rules").click()
    assert pc.opened == ["https://aka.ms/MinecraftEULA"]


def test_a_server_already_on_this_pc_is_added_with_the_folder_picker(qapp, agent_http, tmp_path):
    pc = FakePC()
    window, screen = plus_tab(qapp, agent_http, pc)
    button(screen, "Server I already have").click()
    folder = make_server_folder(tmp_path / "Old", port=25570)
    pc.picked = str(folder)
    button(screen, "Browse…").click()
    screen.findChild(QtWidgets.QLineEdit, "haveName").setText("Old world")
    button(screen, "Add server").click()
    wait_for(
        qapp,
        lambda: window.screen_name == "Server" and "Old world added." in shown_text(window),
        20,
    )
    names = [
        r["name"]
        for r in agent_http.get("/api/servers", headers=owner(agent_http)).json()["servers"]
    ]
    assert "Old world" in names


def test_a_pack_that_cant_be_imported_says_why_and_offers_no_create(qapp, agent_http, tmp_path):
    pc = FakePC()
    _window, screen = plus_tab(qapp, agent_http, pc)
    button(screen, "From a modpack").click()
    bad = tmp_path / "broken.mrpack"
    bad.write_bytes(b"not a zip")
    pc.picked = str(bad)
    button(screen, "Modpack file").click()
    wait_for(qapp, lambda: "Reading the pack" not in shown_text(screen), 20)
    assert not [
        b
        for b in screen.findChildren(QtWidgets.QPushButton)
        if b.text() == "Create server" and b.isVisibleTo(screen)
    ]
