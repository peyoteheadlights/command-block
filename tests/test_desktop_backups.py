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


def test_on_another_pc_an_agent_only_pc_says_to_use_the_phone_app(
    qapp, agent_http, monkeypatch, tmp_path
):
    """An Agent only PC has no app to choose the folder in: say how instead."""
    from agent import appinfo

    (tmp_path / appinfo.INSTALL_RECORD).write_text(
        '{"data_root": "x", "agent_only": true}', encoding="utf-8"
    )
    monkeypatch.setattr(appinfo, "program_dir", lambda: tmp_path)
    window = open_window(qapp, agent_http)
    window.agent.place = "100.64.0.7"
    sign_in(qapp, window)
    wait_for(qapp, lambda: window.screen_name == "All servers" and window.agent_only)
    open_server(qapp, window, "Survival")
    page = open_page(qapp, window, "Backups")
    wait_for(qapp, lambda: "in the Command Block phone app" in shown_text(page))
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


# ---------------------------------------------------------------- backups and disk
def server_dir(agent_http):
    return agent_http.agent.app.state.core.servers["survival"].config.server_dir


def test_a_map_tiles_folder_is_offered_then_left_out(qapp, agent_http):
    tiles = server_dir(agent_http) / "config" / "squaremap" / "web" / "tiles"
    tiles.mkdir(parents=True)
    (tiles / "t.png").write_bytes(b"p" * 5000)
    window, page = backups_page(qapp, agent_http)
    wait_for(qapp, lambda: "squaremap map tiles" in shown_text(page))
    assert "Nothing is left out." in shown_text(page)
    assert "A backup now holds about" in shown_text(page)
    buttons_named(page, "Leave out")[0].click()
    wait_for(qapp, lambda: bool(buttons_named(page, "Back it up again")))
    settings = agent_http.get("/api/servers/survival/settings", headers=owner(agent_http)).json()
    assert settings["backups"]["exclude"] == ["config/squaremap/web/tiles"]


def test_keep_and_restore_as_a_new_server_are_in_a_backups_menu(qapp, agent_http):
    headers = owner(agent_http)
    made = agent_http.post(
        "/api/servers/survival/backups", json={"name": "auto"}, headers=headers, timeout=60
    ).json()
    core = agent_http.agent.app.state.core
    core.db.execute("UPDATE backups SET kind = 'scheduled' WHERE id = ?", (made["id"],))
    window, page = backups_page(qapp, agent_http)
    wait_for(qapp, lambda: "Scheduled" in shown_text(page))
    menu_item(page, "Keep this backup").trigger()
    wait_for(qapp, lambda: "Kept. The clean-up won't delete it." in page.note.text())

    def offered(words):
        try:
            return menu_item(page, words)
        except AssertionError:
            return None

    wait_for(qapp, lambda: offered("Stop keeping this backup") is not None)
    menu_item(page, "Restore as a new server").trigger()
    wait_for(qapp, lambda: "Copying into Survival from" in page.note.text(), 30)


def test_the_clean_up_shows_first_then_moves_to_the_bin_with_undo(qapp, agent_http):
    import os
    import time

    old = server_dir(agent_http) / "logs" / "2026-01-01-1.log.gz"
    old.write_bytes(b"old log")
    week = time.time() - 8 * 86400
    os.utime(old, (week, week))
    window, page = backups_page(qapp, agent_http)
    button(page, "Look for things to clean up").click()

    def picks():
        return [b for b in page.findChildren(QtWidgets.QCheckBox) if "Old log" in b.text()]

    wait_for(qapp, lambda: bool(picks()))
    assert picks()[0].isChecked(), "old logs are ticked to start with"
    assert old.exists(), "only shown so far"
    button(page, "Move to the clean-up bin").click()
    wait_for(qapp, lambda: "Moved 1 to the clean-up bin" in page.note.text())
    assert not old.exists()
    wait_for(qapp, lambda: "Clean-up bin:" in shown_text(page))
    page.note.undo.click()
    wait_for(qapp, lambda: "Put back where it was." in page.note.text())
    assert old.read_bytes() == b"old log"


def test_a_download_cut_half_way_never_takes_the_files_name(tmp_path):
    """Quitting mid-download skips every clean-up; the chosen name only ever
    holds a whole file."""
    kept = tmp_path / "old.zip"
    kept.write_bytes(b"keep me")
    seen: list = []

    def cut_off():
        yield b"half a backup"
        seen.append(kept.read_bytes())  # what the name holds while it downloads
        raise httpx.ReadError("cut off")

    agent = AgentClient(
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=cut_off())),
            base_url="http://x",
        )
    )
    with pytest.raises(AgentError):
        agent.download_to("/servers/survival/backups/1/download", str(kept))
    assert seen == [b"keep me"] and kept.read_bytes() == b"keep me"
    assert list(tmp_path.iterdir()) == [kept]
    whole = AgentClient(
        httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"whole")),
            base_url="http://x",
        )
    )
    whole.download_to("/servers/survival/backups/1/download", str(kept))
    assert kept.read_bytes() == b"whole" and list(tmp_path.iterdir()) == [kept]
