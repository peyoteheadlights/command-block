"""The desktop app's All servers search, groups, order and Start all / Stop
all; the game rules card; the console's history, completion and older
logs; and Open server folder."""

from __future__ import annotations

import gzip

import pytest

from .fake_pc import FakePC
from .test_desktop_app import (
    button,
    on_all_servers,
    open_window,
    owner,
    shown_text,
    sign_in,
    signed_in_on,
    wait_for,
)
from .test_desktop_pages import core_of, running, technical_page
from .test_desktop_settings import card_of, section, settings_page

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")
QtCore = pytest.importorskip("PySide6.QtCore")
QtGui = pytest.importorskip("PySide6.QtGui")


def all_servers(qapp, agent_http, pc=None):
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    return window, window.screens["All servers"]


def names(screen) -> list[str]:
    return [row.server_id for row in screen.placed]


# ---------------------------------------------------------------- All servers
def test_search_narrows_the_list_by_name(qapp, agent_http):
    _window, screen = all_servers(qapp, agent_http)
    assert names(screen) == ["survival", "creative"]
    screen.search.setText("crea")
    assert names(screen) == ["creative"]
    screen.search.setText("zzz")
    wait_for(qapp, lambda: 'No server matches "zzz".' in shown_text(screen))


def test_move_down_and_a_group_are_kept_on_the_agent(qapp, agent_http, monkeypatch):
    window, screen = all_servers(qapp, agent_http)
    screen.arrange("survival", "down")
    wait_for(qapp, lambda: names(screen) == ["creative", "survival"])
    monkeypatch.setattr(window, "ask", lambda *a: "Friends")
    screen.arrange("survival", "group")
    wait_for(
        qapp, lambda: "Friends" in shown_text(screen) and "Other servers" in shown_text(screen)
    )
    headers = owner(agent_http)
    rows = agent_http.get("/api/servers", headers=headers).json()["servers"]
    assert [(r["id"], r["group"]) for r in rows] == [("creative", ""), ("survival", "Friends")]
    # Grouped: Friends first, the rest under Other servers.
    assert names(screen) == ["survival", "creative"]


def test_dropping_a_row_on_the_bottom_half_of_another_puts_it_after(qapp, agent_http):
    _window, screen = all_servers(qapp, agent_http)
    first, second = screen.placed
    data = QtCore.QMimeData()
    data.setData("application/x-command-block-server", b"survival")
    spot = second.mapTo(screen, QtCore.QPoint(10, second.height() - 3))
    drop = QtGui.QDropEvent(
        QtCore.QPointF(spot),
        QtCore.Qt.DropAction.MoveAction,
        data,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    screen.dropEvent(drop)
    wait_for(qapp, lambda: names(screen) == ["creative", "survival"])


def test_start_all_and_stop_all(qapp, agent_http):
    window, screen = all_servers(qapp, agent_http)
    button(screen, "Start all").click()
    wait_for(qapp, lambda: "Started 2." in shown_text(screen))
    headers = owner(agent_http)

    def states() -> set[str]:
        return {
            r["state"] for r in agent_http.get("/api/servers", headers=headers).json()["servers"]
        }

    wait_for(qapp, lambda: states() == {"ONLINE"}, 30, 0.2)
    asked = []
    window.confirm = lambda title, body, yes: asked.append(title) or True
    button(screen, "Stop all").click()
    wait_for(qapp, lambda: "Stopped 2." in shown_text(screen), 30, 0.2)
    assert asked == ["Stop 2 servers?"]
    assert states() == {"OFFLINE"}


def test_a_moderator_sees_no_move_menu(qapp, agent_http):
    window, screen = all_servers(qapp, agent_http)
    window.role = "helper"
    screen.show_all()
    assert all(row.arrange is None for row in screen.placed)


# ---------------------------------------------------------------- Open server folder
def test_open_server_folder_is_in_the_menu_on_this_pc(qapp, agent_http):
    pc = FakePC()
    window = signed_in_on(qapp, agent_http, pc=pc)
    header = window.server_screen.header
    assert header.folder.isVisible()
    header.folder.trigger()
    folder = str(core_of(agent_http).servers["survival"].config.server_dir)
    assert pc.opened == [folder]
    window.agent.place = "100.64.0.9"  # another PC's agent: its path isn't this PC's
    header.show_row(header.row)
    assert not header.folder.isVisible()


# ---------------------------------------------------------------- game rules
def test_game_rules_are_read_when_the_section_opens_and_saved(qapp, agent_http):
    running(qapp, agent_http)
    window, page = settings_page(qapp, agent_http)
    section(page, "Game settings")
    rules = card_of(page, "Game rules")
    wait_for(qapp, lambda: "keepInventory" in shown_text(rules))
    [keep] = [
        s
        for s in page.rules.findChildren(QtWidgets.QAbstractButton)
        if s.accessibleName() == "keepInventory"
    ]
    assert not keep.isChecked()
    keep.setChecked(True)
    button(rules, "Save rules").click()
    wait_for(qapp, lambda: "Changed 1 rules." in shown_text(rules))
    # The page's note has Undo, which sends the old value back.
    assert page.note.undo.isVisible()
    page.note.undo.click()
    wait_for(qapp, lambda: not page.rules.controls["keepInventory"][1]())


def test_a_stopped_server_says_to_start_it(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    section(page, "Game settings")
    wait_for(
        qapp,
        lambda: "Start the server to see its game rules" in shown_text(card_of(page, "Game rules")),
    )


# ---------------------------------------------------------------- console
def test_up_and_down_bring_back_earlier_commands_and_tab_completes(qapp, agent_http):
    running(qapp, agent_http)
    pc = FakePC()
    window, page = technical_page(qapp, agent_http, "Console", pc)
    wait_for(qapp, lambda: "gamerule" in page.usages)
    for words in ("say one", "say two"):
        page.command.setText(words)
        page.send_button.click()
        wait_for(qapp, lambda: page.command.text() == "")
    box = page.command
    up = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_Up, QtCore.Qt.KeyboardModifier.NoModifier
    )
    down = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_Down, QtCore.Qt.KeyboardModifier.NoModifier
    )
    box.setText("draft")
    box.keyPressEvent(up)
    assert box.text() == "say two"
    box.keyPressEvent(up)
    assert box.text() == "say one"
    box.keyPressEvent(down)
    box.keyPressEvent(down)
    assert box.text() == "draft"
    # Kept on this PC for next time.
    assert '"survival": ["say one", "say two"]' in pc.settings["console_history"]

    box.completer().popup().hide()
    box.setText("gamer")
    tab = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_Tab, QtCore.Qt.KeyboardModifier.NoModifier
    )
    box.event(tab)
    assert box.text() == "gamerule "
    assert page.hint.text() == "/gamerule"  # the server's own usage line


def test_older_logs_show_a_zipped_file(qapp, agent_http):
    logs = core_of(agent_http).servers["survival"].config.server_dir / "logs"
    logs.mkdir(exist_ok=True)
    with gzip.open(logs / "2026-11-19-1.log.gz", "wt", encoding="utf-8") as handle:
        handle.write("yesterday's line\n")
    window, page = technical_page(qapp, agent_http, "Console")
    from desktop.console import OlderLogs

    shown = OlderLogs(page)
    shown.show()
    wait_for(qapp, lambda: shown.files.count() >= 1)
    item = shown.files.findItems("2026-11-19-1.log.gz", QtCore.Qt.MatchFlag.MatchExactly)[0]
    shown.files.setCurrentItem(item)
    wait_for(qapp, lambda: "yesterday's line" in shown.text.toPlainText())
    shown.close()
