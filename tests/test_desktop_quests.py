"""The Quests and events page, clicked through against the real agent with
the fake Discord and the fake Minecraft servers."""

from __future__ import annotations

import pytest

from .fake_discord import GUILD_ID, MEMBER_ID, wait_for
from .test_desktop_app import button, owner, shown_text, signed_in_on
from .test_desktop_app import wait_for as wait_for_window
from .test_desktop_discord import discord_http  # noqa: F401  (the fixture)
from .test_desktop_pages import open_page
from .test_discord import fake  # noqa: F401  (the fixture)

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")

ALEX = "069a79f4-44e9-4726-a5be-fca90e38aaf5"


def png() -> bytes:
    """A real little picture, made by Qt."""
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QColor, QImage

    image = QImage(4, 3, QImage.Format.Format_RGB32)
    image.fill(QColor("green"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data.data())


def named(page, kind, name):
    return next(w for w in page.findChildren(kind) if w.accessibleName() == name)


@pytest.fixture
def quests_page(qapp, fake, discord_http):  # noqa: F811
    made = discord_http.post(
        f"/api/discord/guilds/{GUILD_ID}/setup",
        json={"servers": ["survival"]},
        headers=owner(discord_http),
    )
    assert made.status_code == 200, made.text
    core = discord_http.agent.app.state.core
    wait_for(lambda: core.discord.reports.get(GUILD_ID, {}).get("roles"))
    window = signed_in_on(qapp, discord_http)
    page = open_page(qapp, window, "Quests and events")
    wait_for_window(qapp, lambda: "No quests yet" in shown_text(page))
    return window, page, core


def test_a_quest_is_made_from_the_form_and_ended(qapp, quests_page):
    window, page, core = quests_page
    named(page, QtWidgets.QLineEdit, "Title").setText("Night owl")
    kind = named(page, QtWidgets.QComboBox, "What players do")
    kind.setCurrentIndex(kind.findData("joins"))
    named(page, QtWidgets.QSpinBox, "How many").setValue(3)
    repeat = named(page, QtWidgets.QComboBox, "Comes back")
    repeat.setCurrentIndex(repeat.findData("weekly"))
    named(page, QtWidgets.QSpinBox, "Level XP").setValue(50)
    role = named(page, QtWidgets.QComboBox, "Discord role")
    role.setCurrentIndex(role.findText("Friends of Sam"))
    button(page, "Make quest").click()
    wait_for_window(qapp, lambda: "1. Night owl" in shown_text(page))
    text = shown_text(page)
    assert "Join 3 times" in text and "every week" in text
    assert "Discord role Friends of Sam" in text and "50 Level XP" in text
    assert named(page, QtWidgets.QLineEdit, "Title").text() == ""  # the form is empty again
    # A bad quest shows the agent's words in the form.
    kind.setCurrentIndex(kind.findData("stat"))
    named(page, QtWidgets.QLineEdit, "Title").setText("Miner")
    named(page, QtWidgets.QLineEdit, "Which one").setText("not a stat")
    button(page, "Make quest").click()
    wait_for_window(qapp, lambda: "mined:diamond_ore" in shown_text(page))
    asked = []
    window.confirm = lambda title, body, yes: asked.append(title) or True
    button(page, "End quest").click()
    wait_for_window(qapp, lambda: "No quests yet" in shown_text(page))
    assert asked == ["End Night owl?"]
    assert core.quests.active("survival") == []


def test_claims_are_approved_with_their_screenshot_and_ideas_answered(qapp, fake, quests_page):  # noqa: F811
    window, page, core = quests_page
    core.links.link(ALEX, "Alex", "java", "survival", MEMBER_ID, "sam", "test")
    made = window.agent.on("survival", "POST", "/quests", {"title": "Castle", "kind": "approval"})
    quest_id = made["quests"][0]["id"]
    fake.files["/a/castle.png"] = png()
    fake.command(
        "quest done",
        {"quest": quest_id, "note": "by the lake", "screenshot": "9", "server": "survival"},
        resolved={
            "attachments": {"9": {"url": "https://cdn.discordapp.com/a/castle.png", "size": 70}}
        },
    )
    fake.command(
        "quest suggest",
        {"title": "Race to the End", "details": "first one wins", "server": "survival"},
    )
    fake.command("quest suggest", {"title": "Dull idea", "server": "survival"})
    wait_for(
        lambda: core.quests.claims("survival") and len(core.quests.suggestions("survival")) == 2
    )
    page.load()
    wait_for_window(qapp, lambda: 'sam says they did "Castle"' in shown_text(page))
    assert "by the lake" in shown_text(page) and "Race to the End" in shown_text(page)
    button(page, "See the screenshot").click()
    wait_for_window(
        qapp,
        lambda: any(
            lbl.pixmap() is not None and not lbl.pixmap().isNull()
            for lbl in page.findChildren(QtWidgets.QLabel)
        ),
    )
    button(page, "Approve").click()
    wait_for_window(qapp, lambda: "Waiting for your OK" not in shown_text(page))
    assert core.quests.times_finished(quest_id, MEMBER_ID) == 1
    # An idea fills the form; making it answers the idea.
    named(page, QtWidgets.QPushButton, "Make it a quest: Race to the End").click()
    assert named(page, QtWidgets.QLineEdit, "Title").text() == "Race to the End"
    kind = named(page, QtWidgets.QComboBox, "What players do")
    kind.setCurrentIndex(kind.findData("approval"))
    button(page, "Make quest").click()
    wait_for_window(qapp, lambda: "2. Race to the End" in shown_text(page))
    named(page, QtWidgets.QPushButton, "No thanks: Dull idea").click()
    wait_for_window(qapp, lambda: "Quest ideas from players" not in shown_text(page))
    assert core.quests.suggestions("survival") == []


def test_the_page_paints_small_and_large_and_moderators_get_the_level_roles(qapp, quests_page):
    window, page, core = quests_page
    window.agent.on(
        "survival", "POST", "/quests", {"title": "Hours", "kind": "playtime", "count": 5}
    )
    page.load()
    wait_for_window(qapp, lambda: "Play 5 h" in shown_text(page))
    assert "Levels in Friends" in shown_text(page)  # the Level roles box, here too
    for technical, size in ((False, (760, 520)), (True, (1600, 1000))):
        window.set_mode(technical)
        window.resize(*size)
        qapp.processEvents()
        page.grab()
    window.set_mode(False)
    page.load()

    def tick():
        return next(
            (
                b
                for b in page.findChildren(QtWidgets.QCheckBox)
                if b.isVisibleTo(page) and b.text().startswith("Post level-ups")
            ),
            None,
        )

    wait_for_window(qapp, tick)
    tick().setChecked(False)
    button(page, "Save Levels").click()
    wait_for(
        lambda: core.db.get_setting("discord_levels", {}).get(GUILD_ID, {}).get("announce") is False
    )


def test_without_quests_and_events_there_is_no_tab(qapp, fake, discord_http):  # noqa: F811
    from .test_desktop_app import open_server, open_window, sign_in

    made = discord_http.post(
        "/api/accounts",
        json={
            "username": "sam",
            "password": "moderator password",
            "servers": ["survival"],
            "permissions": ["server.view"],
        },
        headers=owner(discord_http),
    )
    assert made.status_code == 200, made.text
    window = open_window(qapp, discord_http)
    sign_in(qapp, window, "sam", "moderator password")
    wait_for_window(qapp, lambda: window.role == "helper" and "Survival" in shown_text(window))
    open_server(qapp, window, "Survival")
    tabs = [b.property("page") for b in window.server_screen.tabs.buttons()]
    assert "Players and chat" in tabs and "Quests and events" not in tabs
