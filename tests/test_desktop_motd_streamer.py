"""The desktop app's MOTD builder, server icon picker, Java options Undo and
streamer mode."""

from __future__ import annotations

import json
import struct

import pytest

from .test_desktop_app import button, shown_text, wait_for
from .test_desktop_pages import core_of
from .test_desktop_settings import card_of, section, settings_page

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


# ---------------------------------------------------------------- the MOTD
def test_the_preview_draws_minecrafts_colour_codes():
    from desktop.motd import preview_html

    drawn = preview_html("§6§lGold <b>\n§rplain")
    assert "color:#FFAA00;font-weight:bold" in drawn
    assert "&lt;b&gt;" in drawn and "<br>" in drawn  # typed text is never markup
    assert drawn.endswith('<span style="color:#AAAAAA;">plain</span>')
    # A colour ends bold, as in Java Edition.
    assert "font-weight" not in preview_html("§lA§cB").split("</span>")[1]


def test_the_motd_card_saves_two_lines_with_a_colour(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    shown = card_of(section(page, "Game settings"), "How it shows in the server list")
    first, second = shown.findChildren(QtWidgets.QLineEdit)[:2]
    first.setText("Hello")
    first.setCursorPosition(0)
    [gold] = [b for b in shown.findChildren(QtWidgets.QPushButton) if b.toolTip() == "Gold"]
    gold.click()
    second.setText("come in")
    button(shown, "Save").click()
    wait_for(qapp, lambda: "Saved." in shown_text(page))
    folder = core_of(agent_http).servers["survival"].config.server_dir
    assert "motd=\\u00A76Hello\\u000Acome in" in (folder / "server.properties").read_text()
    # The form itself no longer has its own MOTD box.
    game = card_of(page, "Game settings")
    assert "Server description" not in shown_text(game)


# ---------------------------------------------------------------- the icon
def test_any_picture_becomes_a_64_pixel_png(qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage

    from desktop.motd import icon_png

    wide = QImage(300, 120, QImage.Format.Format_RGB32)
    wide.fill(QColor("red"))
    wide.save(str(tmp_path / "wide.jpg"))
    png = icon_png(str(tmp_path / "wide.jpg"))
    assert png and png.startswith(b"\x89PNG") and struct.unpack(">II", png[16:24]) == (64, 64)
    (tmp_path / "notes.txt").write_text("not a picture")
    assert icon_png(str(tmp_path / "notes.txt")) is None


def test_picking_an_icon_saves_it_and_remove_takes_it_away(qapp, agent_http, tmp_path, monkeypatch):
    from PySide6.QtGui import QColor, QImage

    picture = QImage(128, 128, QImage.Format.Format_RGB32)
    picture.fill(QColor("blue"))
    picture.save(str(tmp_path / "logo.png"))
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(tmp_path / "logo.png"), "")
    )
    _window, page = settings_page(qapp, agent_http)
    shown = card_of(section(page, "Game settings"), "How it shows in the server list")
    button(shown, "Pick a picture").click()
    wait_for(qapp, lambda: "Server icon changed." in shown_text(page))
    icon = core_of(agent_http).servers["survival"].config.server_dir / "server-icon.png"
    assert struct.unpack(">II", icon.read_bytes()[16:24]) == (64, 64)

    def remove_shown() -> bool:
        card = card_of(page, "How it shows in the server list")
        return any(
            b.text() == "Remove" and b.isVisibleTo(card)
            for b in card.findChildren(QtWidgets.QPushButton)
        )

    wait_for(qapp, remove_shown)
    shown = card_of(page, "How it shows in the server list")
    button(shown, "Remove").click()
    wait_for(qapp, lambda: "Server icon removed." in shown_text(page))
    assert not icon.exists()


# ---------------------------------------------------------------- Java options
def test_java_options_warn_refuse_a_bad_one_and_undo(qapp, agent_http):
    window, page = settings_page(qapp, agent_http)
    window.set_mode(True)
    page.load()
    section(page, "Game settings")
    wait_for(qapp, lambda: "Bad flags can stop the JVM starting" in shown_text(page))
    memory = card_of(page, "Memory (-Xmx)")
    args = next(e for e in memory.findChildren(QtWidgets.QLineEdit) if "-Xmx" in e.text())
    before = args.text().split()
    args.setText(args.text() + " nogui")
    button(memory, "Save JVM arguments").click()
    wait_for(qapp, lambda: "'nogui' isn't a Java argument" in shown_text(page))
    config = core_of(agent_http).servers["survival"].config
    args.setText(" ".join(before) + " -XX:+UseG1GC")
    button(memory, "Save JVM arguments").click()
    wait_for(qapp, lambda: "-XX:+UseG1GC" in config.server.jvm_args)
    wait_for(
        qapp,
        lambda: any(
            b.text() == "Undo" and b.isVisibleTo(page)
            for b in page.findChildren(QtWidgets.QPushButton)
        ),
    )
    button(page, "Undo").click()
    wait_for(qapp, lambda: list(config.server.jvm_args) == before)
    window.set_mode(False)


# ---------------------------------------------------------------- streamer mode
def test_the_shared_cases_hide_what_they_should():
    from desktop.streamer import hide

    cases = json.loads(open("shared/streamer.json", encoding="utf-8").read())["cases"]
    for shown, hidden in cases:
        assert hide(shown) == hidden, shown
    # In rich text a link's target stays as it is.
    assert hide('<a href="x">1.2.3.4</a>') == '<a href="x">••••</a>'


def test_streamer_mode_covers_labels_and_boxes_and_comes_off(qapp):
    from desktop import streamer

    holder = QtWidgets.QWidget()
    column = QtWidgets.QVBoxLayout(holder)
    words = QtWidgets.QLabel("Join at 203.0.113.5:25565")
    typed = QtWidgets.QLineEdit("C:\\Servers\\Survival")
    plain = QtWidgets.QLabel("Steve joined the game")
    for widget in (words, typed, plain):
        column.addWidget(widget)
    holder.show()
    try:
        streamer.turn(True)
        qapp.processEvents()
        assert words.text() == "Join at ••••"
        assert plain.text() == "Steve joined the game"
        # A box someone types in keeps its words (Save must never save dots).
        assert typed.text() == "C:\\Servers\\Survival"
        assert typed.echoMode() == QtWidgets.QLineEdit.EchoMode.Password
        words.setText("Now at 198.51.100.7")  # set again later by a page
        words.repaint()
        qapp.processEvents()
        assert words.text() == "Now at ••••"
        assert streamer.shown("from 10.0.0.2") == "from ••••"
    finally:
        streamer.turn(False)
    assert typed.echoMode() == QtWidgets.QLineEdit.EchoMode.Normal
    assert streamer.shown("from 10.0.0.2") == "from 10.0.0.2"
    holder.close()
