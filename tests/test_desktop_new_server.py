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
            window.screen_name == "New server"
            and any(
                w in shown_text(screen) for w in ("Which kind of server?", "Which server type?")
            )
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


def test_in_technical_a_server_can_still_be_added_where_it_is(qapp, agent_http, tmp_path):
    pc = FakePC()
    window, screen = plus_tab(qapp, agent_http, pc)
    window.set_mode(True)
    screen.load()
    wait_for(qapp, lambda: "Which server type?" in shown_text(screen))
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


def test_a_goal_marks_the_kinds_that_fit_and_paper_warns_about_farms(qapp, agent_http, monkeypatch):
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(fake_sources()))
    _window, screen = plus_tab(qapp, agent_http)
    assert "Leaf" not in shown_text(screen), "forks only in Technical"
    assert "Exactly like the game." in shown_text(screen)
    radio = next(
        b
        for b in screen.findChildren(QtWidgets.QRadioButton)
        if b.text() == "Plain vanilla with friends"
    )
    radio.setChecked(True)
    wait_for(qapp, lambda: screen.advice is not None, 20)
    assert screen.advice["recommended"] == ["vanilla", "fabric"]
    screen.choose_buttons["paper"].click()
    wait_for(qapp, lambda: "farms may work differently" in shown_text(screen), 20)


def test_a_server_folder_is_read_then_brought_in(qapp, agent_http, tmp_path):
    pc = FakePC()
    window, screen = plus_tab(qapp, agent_http, pc)
    button(screen, "Server I already have").click()
    folder = make_server_folder(tmp_path / "Old SMP", port=25570)
    pc.picked = str(folder)
    button(screen, "Choose a folder").click()
    button(screen, "Read it").click()
    wait_for(qapp, lambda: "Found: Fabric, 0 mods, 1 world." in shown_text(screen), 20)
    pc.picked = str(tmp_path / "Copy")
    button(screen, "Browse…").click()
    button(screen, "Bring it in").click()
    wait_for(qapp, lambda: "Bringing Old SMP in." in shown_text(screen), 20)
    wait_for(
        qapp,
        lambda: (
            "Old SMP"
            in [
                r["name"]
                for r in agent_http.get("/api/servers", headers=owner(agent_http)).json()["servers"]
            ]
        ),
        30,
    )
    assert (tmp_path / "Copy" / "fabric-server-launch.jar").is_file()
