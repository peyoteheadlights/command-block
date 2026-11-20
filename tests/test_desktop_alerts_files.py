"""The desktop app's Server files card, Save as CSV, the crash and join
sounds, and the Technical exit codes and priority settings."""

from __future__ import annotations

import pytest

from .fake_pc import FakePC
from .test_desktop_app import button, shown_text, signed_in_on, wait_for
from .test_desktop_pages import core_of, open_page, technical_page
from .test_desktop_settings import card_of, settings_page

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


def server_folder(agent_http):
    return core_of(agent_http).servers["survival"].config.server_dir


def test_server_files_download_and_upload(qapp, agent_http, tmp_path):
    folder = server_folder(agent_http)
    (folder / "plugins").mkdir(exist_ok=True)
    (folder / "plugins" / "notes.txt").write_text("hi")
    pc = FakePC()
    window = signed_in_on(qapp, agent_http, pc=pc)
    page = open_page(qapp, window, "Settings")
    card = window.server_screen.page_widgets["Settings"].server_files
    listed = card.listed
    wait_for(qapp, lambda: any(listed.item(i).text() == "plugins/" for i in range(listed.count())))
    assert card_of(page, "Server files")

    folder_item = next(
        listed.item(i) for i in range(listed.count()) if listed.item(i).text() == "plugins/"
    )
    card.open_item(folder_item)
    wait_for(qapp, lambda: card.path == "plugins" and listed.count() == 1)
    assert card.where.text() == "/plugins"
    listed.setCurrentRow(0)
    pc.save_to = str(tmp_path / "notes.txt")
    card.download.click()
    wait_for(qapp, lambda: (tmp_path / "notes.txt").exists())
    assert (tmp_path / "notes.txt").read_text() == "hi"

    upload = tmp_path / "extra.yml"
    upload.write_text("a: 1\n")
    pc.picked = str(upload)
    card.upload.click()
    wait_for(qapp, lambda: (folder / "plugins" / "extra.yml").exists())
    wait_for(qapp, lambda: listed.count() == 2)

    card.up.click()
    wait_for(qapp, lambda: card.path == "" and not card.up.isEnabled())


def test_save_as_csv(qapp, agent_http):
    pc = FakePC()
    _window, page = technical_page(qapp, agent_http, "Performance", pc=pc)
    button(page, "Export CSV").click()
    wait_for(qapp, lambda: any(name.endswith(".csv") for name in pc.files))
    text = next(data for name, data in pc.files.items() if name.endswith(".csv")).decode()
    assert text.startswith("time,pc_cpu_percent,")
    wait_for(qapp, lambda: "Saved:" in shown_text(page))


def test_crash_and_join_sounds_only_when_picked(qapp, agent_http):
    pc = FakePC()
    window = signed_in_on(qapp, agent_http, pc=pc)
    crash = {"type": "event", "event": {"type": "server_crashed", "server_id": "survival"}}
    join = {"type": "event", "event": {"type": "player_joined", "server_id": "survival"}}
    window.feed_message(crash)
    window.feed_message(join)
    assert pc.sounds == []  # off unless picked
    pc.settings["sound_crash"] = True
    window.feed_message(crash)
    window.feed_message(join)
    assert pc.sounds == [True]
    pc.settings["sound_join"] = "true"  # how QSettings gives it back
    window.feed_message(join)
    assert pc.sounds == [True, False]


def test_technical_shows_exit_codes_and_priority(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    assert "Lower the server's priority" not in shown_text(page)
    _window, page = technical_page(qapp, agent_http, "Settings")
    wait_for(qapp, lambda: "Below normal process priority" in shown_text(page))
    codes = next(
        box
        for box in page.findChildren(QtWidgets.QLineEdit)
        if box.accessibleName() == "Ignored exit codes"
    )
    codes.setText("130, 1")
    crashes = codes.parentWidget()
    while crashes.property("kind") != "card":
        crashes = crashes.parentWidget()
    button(crashes, "Save").click()
    monitor = core_of(agent_http).servers["survival"].config
    wait_for(qapp, lambda: monitor.monitor.ignored_exit_codes == "1, 130")
