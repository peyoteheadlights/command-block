"""The desktop app's Backups page (backups, Restore world, the
second copy, bringing a world in and taking it home, drag and drop), driven
like a person would against the real agent with the fake Minecraft servers."""

from __future__ import annotations

import zipfile

import httpx
import pytest

from desktop.agent import AgentClient, AgentError, Unreachable

from .fake_pc import FakePC
from .test_desktop_app import button, open_server, open_window, owner, shown_text, sign_in, wait_for
from .test_desktop_pages import buttons_named, open_page, running

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


def backups_page(qapp, agent_http, pc=None):
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    wait_for(qapp, lambda: window.screen_name == "All servers" and "Survival" in shown_text(window))
    open_server(qapp, window, "Survival")
    return window, open_page(qapp, window, "Backups")


def make_backup(agent_http, headers) -> dict:
    made = agent_http.post("/api/servers/survival/backups", json={}, headers=headers, timeout=60)
    assert made.status_code == 200, made.text
    return made.json()


def menu_item(page, words):
    """An item of a backup's ⋯ menu."""
    for more in page.findChildren(QtWidgets.QToolButton):
        menu = more.menu()
        for action in menu.actions() if menu else []:
            if action.text() == words:
                return action
    raise AssertionError(f"no {words!r} in a backup's menu")


def asked(window, answer=True) -> list[tuple[str, str]]:
    """Answers the window's questions, and records them as (title, body)."""
    questions: list[tuple[str, str]] = []

    def confirm(title, body, yes):
        questions.append((title, body))
        return answer

    window.confirm = confirm
    return questions


def world_zip(tmp_path) -> str:
    path = tmp_path / "Islands.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Islands/level.dat", b"x" * 2048)
        z.writestr("Islands/region/r.0.0.mca", b"r" * 4096)
    return str(path)


def undo_titles(agent_http, headers) -> list[str]:
    changes = agent_http.get("/api/servers/survival/changes", headers=headers).json()
    return [c["title"] for c in changes["undo"]]


# ---------------------------------------------------------------- backups
def test_back_up_now_lists_the_backup_and_the_last_backup_card_opens_backups(qapp, agent_http):
    window, page = backups_page(qapp, agent_http)
    wait_for(qapp, lambda: "No backups yet" in shown_text(page))
    button(page, "Back up now").click()
    wait_for(qapp, lambda: "Backup saved and checked" in page.note.text(), 60)
    wait_for(qapp, lambda: "Manual" in shown_text(page) and "KB" in shown_text(page))
    assert "Second copy:" not in shown_text(page)  # no folder chosen
    assert "Old backups delete automatically" in shown_text(page)

    window.open_page("Overview")
    button(window.server_screen.simple_overview, "See backups").click()
    assert window.server_screen.current_page == "Backups"


def test_delete_asks_first_then_the_backup_is_gone(qapp, agent_http):
    make_backup(agent_http, owner(agent_http))
    window, page = backups_page(qapp, agent_http)
    wait_for(qapp, lambda: "Manual" in shown_text(page))
    questions = asked(window, answer=False)
    menu_item(page, "Delete").trigger()
    assert questions and questions[0][0].startswith("Delete ")
    qapp.processEvents()
    assert "Manual" in shown_text(page)
    asked(window)
    menu_item(page, "Delete").trigger()
    wait_for(qapp, lambda: "No backups yet" in shown_text(page))


def test_download_writes_the_backup_where_the_save_box_said(qapp, agent_http, tmp_path):
    headers = owner(agent_http)
    made = make_backup(agent_http, headers)
    pc = FakePC()
    pc.save_to = str(tmp_path / "copy.zip")
    _window, page = backups_page(qapp, agent_http, pc)
    wait_for(qapp, lambda: "Manual" in shown_text(page))
    menu_item(page, "Download").trigger()
    wait_for(qapp, lambda: "Saved to" in page.note.text(), 30)
    original = agent_http.get(
        f"/api/servers/survival/backups/{made['id']}/download", headers=headers
    ).content
    assert (tmp_path / "copy.zip").read_bytes() == original


# ---------------------------------------------------------------- restoring
def test_restore_world_asks_with_what_is_lost_then_undo_is_offered(qapp, agent_http):
    headers = owner(agent_http)
    make_backup(agent_http, headers)
    window, page = backups_page(qapp, agent_http)
    wait_for(qapp, lambda: buttons_named(page, "Restore world"))
    questions = asked(window)
    button(page, "Restore world").click()
    [(title, body)] = questions
    assert title.startswith("Restore the world to")
    assert "Mods and settings stay as they are" in body and "ago" in body
    wait_for(qapp, lambda: "The world was put back" in page.note.text(), 60)
    assert page.note.undo.isVisible()
    assert undo_titles(agent_http, headers)


def test_restore_world_waits_while_the_server_runs(qapp, agent_http):
    headers = running(qapp, agent_http)
    make_backup(agent_http, headers)
    _window, page = backups_page(qapp, agent_http)
    wait_for(qapp, lambda: buttons_named(page, "Restore world"))
    assert not button(page, "Restore world").isEnabled()
    assert "Stop the server first" in shown_text(page)


def test_restore_everything_shows_the_agents_steps_then_restores(qapp, agent_http):
    make_backup(agent_http, owner(agent_http))
    window, page = backups_page(qapp, agent_http)
    wait_for(qapp, lambda: "Manual" in shown_text(page))
    questions = asked(window)
    menu_item(page, "Restore everything").trigger()
    wait_for(qapp, lambda: questions)
    assert "A safety backup of the current world will be created first" in questions[0][1]
    wait_for(qapp, lambda: "restored" in page.note.text(), 60)


# ---------------------------------------------------------------- second copy
def test_the_second_copy_folder_is_chosen_with_windows_own_picker(qapp, agent_http, tmp_path):
    pc = FakePC()
    pc.picked = str(tmp_path / "USB")
    (tmp_path / "USB").mkdir()
    _window, page = backups_page(qapp, agent_http, pc)
    wait_for(qapp, lambda: "Off. Backups are only on this PC." in shown_text(page))
    page.choose.click()  # the second copy's; Bring a world in has one too
    assert pc.picker_titles == ["Where should the second copies go?"]
    wait_for(qapp, lambda: "New backups are also copied to" in shown_text(page))
    button(page, "Turn off").click()
    wait_for(qapp, lambda: "Off. Backups are only on this PC." in shown_text(page))


def test_on_another_pc_the_second_copy_folder_is_chosen_on_the_servers_pc(qapp, agent_http):
    window = open_window(qapp, agent_http)
    window.agent.place = "100.64.0.7"  # a moderator's PC, reaching the agent over Tailscale
    sign_in(qapp, window)
    wait_for(qapp, lambda: window.screen_name == "All servers" and "Survival" in shown_text(window))
    open_server(qapp, window, "Survival")
    page = open_page(qapp, window, "Backups")
    wait_for(qapp, lambda: "choose it on that PC" in shown_text(page))
    assert not page.choose.isVisibleTo(page)


# ---------------------------------------------------------------- bringing a world in
def test_a_world_zip_is_read_first_then_replaces_the_world_with_undo(qapp, agent_http, tmp_path):
    headers = owner(agent_http)
    pc = FakePC()
    pc.picked = world_zip(tmp_path)
    window, page = backups_page(qapp, agent_http, pc)
    questions = asked(window, answer=False)
    button(page, "Choose a .zip").click()
    wait_for(qapp, lambda: questions, 30)
    title, body = questions[0]
    assert title == 'Use "Islands" as this server\'s world?'
    assert "Size: " in body and "backed up first" in body
    assert not undo_titles(agent_http, headers)  # cancelled: nothing changed

    questions = asked(window)
    button(page, "Choose a .zip").click()
    wait_for(qapp, lambda: "World brought in" in page.note.text(), 60)
    assert page.note.undo.isVisible()
    assert undo_titles(agent_http, headers)


def test_a_world_folder_on_another_pc_is_zipped_and_sent(qapp, agent_http, tmp_path):
    folder = tmp_path / "Farm"
    (folder / "region").mkdir(parents=True)
    (folder / "level.dat").write_bytes(b"x" * 2048)
    pc = FakePC()
    pc.picked = str(folder)
    window = open_window(qapp, agent_http, pc)
    window.agent.place = "100.64.0.7"
    sign_in(qapp, window)
    wait_for(qapp, lambda: window.screen_name == "All servers" and "Survival" in shown_text(window))
    open_server(qapp, window, "Survival")
    page = open_page(qapp, window, "Backups")
    questions = asked(window)
    button(page, "Choose a folder").click()
    wait_for(qapp, lambda: "World brought in" in page.note.text(), 60)
    assert questions[0][0] == 'Use "Farm" as this server\'s world?'


def test_dropping_a_world_folder_on_the_window_asks_to_bring_it_in(qapp, agent_http, tmp_path):
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent

    folder = tmp_path / "Dropped"
    folder.mkdir()
    (folder / "level.dat").write_bytes(b"x" * 2048)
    window, _page = backups_page(qapp, agent_http)
    window.open_page("Overview")
    questions = asked(window, answer=False)
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(folder))])
    drop = QDropEvent(
        QPointF(10, 10),
        Qt.DropAction.CopyAction,
        data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.dropEvent(drop)
    assert window.server_screen.current_page == "Backups"
    wait_for(qapp, lambda: questions, 30)
    assert questions[0][0] == 'Use "Dropped" as this server\'s world?'


def test_bringing_a_world_in_waits_while_the_server_runs(qapp, agent_http):
    running(qapp, agent_http)
    _window, page = backups_page(qapp, agent_http)
    wait_for(qapp, lambda: "Stop the server first." in shown_text(page))
    assert not button(page, "Choose a .zip").isEnabled()


def test_take_the_world_home_saves_a_zip(qapp, agent_http, tmp_path):
    pc = FakePC()
    pc.save_to = str(tmp_path / "home.zip")
    _window, page = backups_page(qapp, agent_http, pc)
    button(page, "Download world").click()
    wait_for(qapp, lambda: "Saved to" in page.note.text(), 60)
    with zipfile.ZipFile(tmp_path / "home.zip") as z:
        assert any(name.endswith("level.dat") for name in z.namelist())


# ---------------------------------------------------------------- slow work
def test_slow_work_that_gets_no_answer_says_still_working_not_unreachable():
    def never_answers(request):
        raise httpx.ReadTimeout("no answer", request=request)

    agent = AgentClient(
        httpx.Client(transport=httpx.MockTransport(never_answers), base_url="http://x")
    )
    with pytest.raises(AgentError) as slow:
        agent.call("POST", "/servers/survival/backups", {}, slow=True)
    assert not isinstance(slow.value, Unreachable) and "still be working" in str(slow.value)
    with pytest.raises(Unreachable):
        agent.call("GET", "/servers")


def test_a_folder_without_level_dat_is_not_sent(qapp, agent_http, tmp_path):
    (tmp_path / "Documents").mkdir()
    pc = FakePC()
    pc.picked = str(tmp_path / "Documents")
    window = open_window(qapp, agent_http, pc)
    window.agent.place = "100.64.0.7"
    sign_in(qapp, window)
    wait_for(qapp, lambda: window.screen_name == "All servers" and "Survival" in shown_text(window))
    open_server(qapp, window, "Survival")
    page = open_page(qapp, window, "Backups")
    wait_for(qapp, lambda: buttons_named(page, "Choose a folder"))
    button(page, "Choose a folder").click()
    assert "isn't a Minecraft world" in page.note.text()


def test_a_failed_download_leaves_the_file_the_save_box_chose(tmp_path):
    kept = tmp_path / "old.zip"
    kept.write_bytes(b"keep me")
    agent = AgentClient(
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(404, json={"detail": "Gone."})),
            base_url="http://x",
        )
    )
    with pytest.raises(AgentError, match="Gone."):
        agent.download_to("/servers/survival/backups/1/download", str(kept))
    assert kept.read_bytes() == b"keep me"
