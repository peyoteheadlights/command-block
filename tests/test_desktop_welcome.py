"""The desktop app's first-run questions ("Is this your first time?",
"Who's this server for?") and where their answers end up: the mode, the
PC settings card and a new server's whitelist. Against the real agent."""

from __future__ import annotations

import httpx
import pytest

from agent import downloads
from agent.servertypes import create as create_module

from .test_desktop_app import button, on_all_servers, open_window, owner, sign_in, wait_for
from .test_server_types import MC, fake_sources

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


def unanswered(agent_http):
    agent_http.agent.app.state.core.db.set_setting("user:admin:welcome", None)


def questions(qapp, agent_http):
    unanswered(agent_http)
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    wait_for(
        qapp, lambda: window.findChild(QtWidgets.QDialog, "") is not None and window.welcome_open
    )
    from desktop.welcome import WelcomeDialog

    [dialog] = window.findChildren(WelcomeDialog)
    return window, dialog


def welcome(agent_http):
    return agent_http.get("/api/account/welcome", headers=owner(agent_http)).json()


def test_a_first_timer_answers_both_and_gets_simple_mode(qapp, agent_http):
    window, dialog = questions(qapp, agent_http)
    assert "only while this PC is on" in " ".join(
        w.text() for w in dialog.findChildren(QtWidgets.QLabel)
    )
    dialog.findChild(QtWidgets.QPushButton, "welcomeYes").click()
    dialog.findChild(QtWidgets.QPushButton, "welcomeDone").click()
    assert "Pick one" in dialog.said.text()  # nothing is guessed for them
    dialog.who.button(1).setChecked(True)  # My kids and their friends
    dialog.findChild(QtWidgets.QPushButton, "welcomeDone").click()
    wait_for(qapp, lambda: not window.welcome_open)
    assert welcome(agent_http) == {
        "answered": True,
        "first_time": True,
        "kinds": [],
        "played_by": "kids",
    }
    from desktop.words import mode

    wait_for(qapp, lambda: mode["technical"] is False and window.tips_on)


def test_no_asks_which_kinds_were_made_before(qapp, agent_http):
    window, dialog = questions(qapp, agent_http)
    dialog.findChild(QtWidgets.QPushButton, "welcomeNo").click()
    assert dialog.pages.currentIndex() == 1
    dialog.kinds["plugins"].setChecked(True)
    dialog.findChild(QtWidgets.QPushButton, "welcomeKindsNext").click()
    dialog.who.button(2).setChecked(True)
    dialog.findChild(QtWidgets.QPushButton, "welcomeDone").click()
    wait_for(qapp, lambda: not window.welcome_open)
    assert welcome(agent_http)["kinds"] == ["plugins"]
    assert welcome(agent_http)["played_by"] == "community"


def test_i_know_what_im_doing_skips_both_from_the_first_question(qapp, agent_http):
    window, dialog = questions(qapp, agent_http)
    dialog.findChild(QtWidgets.QPushButton, "welcomeSkip").click()
    wait_for(qapp, lambda: not window.welcome_open)
    from desktop.words import mode

    wait_for(qapp, lambda: mode["technical"] is True)
    assert welcome(agent_http)["first_time"] is None
    assert not window.tips_on
    mode["technical"] = False  # other tests start in Simple


def test_a_set_up_pc_is_not_asked_again(qapp, agent_http):
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    qapp.processEvents()
    assert not window.welcome_open


def test_a_new_server_for_kids_takes_their_names(qapp, agent_http, monkeypatch, tmp_path):
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(fake_sources()))
    monkeypatch.setattr(create_module, "java_problem", lambda *args, **kwargs: None)
    agent_http.agent.app.state.core.db.set_setting("played_by_default", "kids")
    from .test_desktop_new_server import plus_tab

    window, screen = plus_tab(qapp, agent_http)
    screen.choose_buttons["vanilla"].click()
    wait_for(qapp, lambda: screen.choice["minecraft"] == MC, 20)
    wait_for(qapp, lambda: screen.findChild(QtWidgets.QLineEdit, "newName") is not None)
    who, names = screen.who, screen.names  # the details card is made again per version
    assert who.currentData() == "kids"  # this PC's first-run answer
    assert names.isVisibleTo(screen)
    names.setText("Alex, Sam;  ")
    screen.findChild(QtWidgets.QLineEdit, "newName").setText("Kids World")
    folder = tmp_path / "kids"
    screen.folder.setText(str(folder))
    screen.findChild(QtWidgets.QCheckBox, "newEula").setChecked(True)
    button(screen, "Create server").click()
    wait_for(qapp, lambda: window.screen_name == "Server", 30)
    assert "white-list=true" in (folder / "server.properties").read_text(encoding="utf-8")
    waiting = agent_http.agent.app.state.core.db.get_setting("whitelist_additions", {})
    assert list(waiting.values()) == [["Alex", "Sam"]]
    who.setCurrentIndex(who.findData("community"))
    assert not names.isVisibleTo(screen)
