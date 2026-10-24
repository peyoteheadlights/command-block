"""The desktop app's Discord section and the Players page's Linked accounts,
against the real agent with the fake Discord (tests/fake_discord.py)."""

from __future__ import annotations

import pytest

from .fake_discord import MEMBER_ID, wait_for
from .test_desktop_app import button, shown_text
from .test_desktop_app import wait_for as wait_for_window
from .test_desktop_gear import gear, listed, shown_buttons
from .test_desktop_pages import open_page
from .test_discord import fake  # noqa: F401  (the fixture)

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


@pytest.fixture
def discord_http(fake, agent_http, tmp_path):  # noqa: F811
    core = agent_http.agent.app.state.core
    core.config.env_path = tmp_path / ".env"  # never the project's own .env
    wait_for(
        lambda: core.discord and core.discord.gateway.state == "connected" and core.discord.reports
    )
    return agent_http


def test_the_discord_section_sets_up_a_discord_server(qapp, fake, discord_http):  # noqa: F811
    window, page = gear(qapp, discord_http, "discord")
    assert "Discord" in listed(window)
    wait_for_window(qapp, lambda: "Connected as Command Block." in shown_text(page))
    assert "Friends" in shown_text(page) and "Invite the bot" in shown_buttons(page)
    # The bot's token steps only show until it works.
    assert "Open the developer portal" not in shown_buttons(page)
    survival = [b for b in page.findChildren(QtWidgets.QCheckBox) if b.text() == "Survival"][0]
    survival.setChecked(True)
    button(page, "Set up this Discord server").click()
    wait_for_window(qapp, lambda: "Made:" in shown_text(page), 20)
    wait_for_window(qapp, lambda: "Everything the bot needs is in place." in shown_text(page))
    names = {c["name"] for c in fake.channels}
    assert {"alerts", "chat", "status"} <= names
    # The new mod role got the default level, shown with the moderators' rows.
    assert "Minecraft mod" in shown_text(page)
    page.grab()  # paints without errors


def test_linked_accounts_show_on_the_players_page(qapp, fake, discord_http):  # noqa: F811
    from .test_desktop_app import signed_in_on

    core = discord_http.agent.app.state.core
    core.links.link(
        "069a79f4-44e9-4726-a5be-fca90e38aaf5", "Alex", "java", "survival", MEMBER_ID, "sam", "t"
    )
    window = signed_in_on(qapp, discord_http)
    page = open_page(qapp, window, "Players and chat")
    wait_for_window(qapp, lambda: "Alex: sam on Discord" in shown_text(page))
    button(page, "Remove").click()
    wait_for_window(qapp, lambda: not core.links.of_server("survival"))
