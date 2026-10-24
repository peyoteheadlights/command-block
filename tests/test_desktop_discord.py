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


def test_the_address_to_share_and_the_webhook_switch_are_saved(qapp, fake, discord_http):  # noqa: F811
    window, page = gear(qapp, discord_http, "discord")
    wait_for_window(qapp, lambda: "Address to share for Survival" in shown_text(page))
    box = next(
        b
        for b in page.findChildren(QtWidgets.QLineEdit)
        if b.accessibleName() == "Address to share for Survival"
    )
    box.setText("play.example.com")
    save = next(
        b
        for b in page.findChildren(QtWidgets.QPushButton)
        if b.accessibleName() == "Save: Address to share for Survival"
    )
    save.click()
    core = discord_http.agent.app.state.core
    wait_for_window(qapp, lambda: core.db.get_setting("discord_addresses", {}))
    assert core.db.get_setting("discord_addresses", {}) == {"survival": "play.example.com"}
    both = next(
        b for b in page.findChildren(QtWidgets.QCheckBox) if b.text().startswith("Also send")
    )
    both.setChecked(True)
    wait_for_window(qapp, lambda: core.config.notifications.webhook_with_bot)


def test_a_start_request_alert_has_start_and_say_no(qapp, fake, discord_http):  # noqa: F811
    from .fake_discord import GUILD_ID
    from .fake_pc import FakePC
    from .test_desktop_app import on_all_servers, open_window, owner, server_state, sign_in

    pc = FakePC()
    window = open_window(qapp, discord_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    headers = owner(discord_http)
    made = discord_http.post(
        f"/api/discord/guilds/{GUILD_ID}/setup", json={"servers": ["survival"]}, headers=headers
    )
    assert made.status_code == 200, made.text
    window.hide()
    token = fake.command("request-start", {"server": "survival"})
    assert wait_for(lambda: fake.replies(token))[0].startswith("Asked.")
    wait_for_window(qapp, lambda: any("start request" in t for t, _b in pc.alerts), 30)
    asked = next(i for i, (t, _b) in enumerate(pc.alerts) if "start request" in t)
    assert pc.buttons(asked) == ["Open", "Start", "Say no"]
    pc.click_alert("Start", asked)
    wait_for_window(
        qapp,
        lambda: server_state(discord_http, "survival", headers) in ("STARTING", "ONLINE"),
        30,
        0.2,
    )
    told = wait_for(lambda: [m for m in fake.messages if "is starting" in m.get("content", "")])
    assert told[0]["content"].startswith(f"<@{MEMBER_ID}>")
    discord_http.post("/api/servers/survival/server/stop", json={}, headers=headers)


def test_level_settings_are_changed_and_saved_on_the_discord_page(qapp, fake, discord_http):  # noqa: F811
    from agent.discord import layout

    from .fake_discord import GUILD_ID
    from .test_desktop_app import owner

    made = discord_http.post(
        f"/api/discord/guilds/{GUILD_ID}/setup",
        json={"servers": ["survival"]},
        headers=owner(discord_http),
    )
    assert made.status_code == 200, made.text
    core = discord_http.agent.app.state.core
    window, page = gear(qapp, discord_http, "discord")
    wait_for_window(qapp, lambda: "Members get XP for playing" in shown_text(page))
    for technical, size in ((False, (760, 520)), (True, (1600, 1000))):
        window.set_mode(technical)
        window.resize(*size)
        qapp.processEvents()
        page.grab()  # paints, small and large, in both modes
    window.set_mode(False)
    wait_for_window(qapp, lambda: "Members get XP for playing" in shown_text(page))
    tick = next(
        b for b in page.findChildren(QtWidgets.QCheckBox) if b.text().startswith("Post level-ups")
    )
    tick.setChecked(False)
    day = next(
        b
        for b in page.findChildren(QtWidgets.QComboBox)
        if b.accessibleName() == "Weekly leaderboard"
    )
    day.setCurrentIndex(day.findData(4))
    legend = next(b for b in page.findChildren(QtWidgets.QSpinBox) if b.value() == 25)
    legend.setValue(20)
    next(
        b
        for b in page.findChildren(QtWidgets.QPushButton)
        if b.accessibleName() == "Remove: Level 10"
    ).click()
    button(page, "Save Levels").click()
    wait_for_window(qapp, lambda: layout.level_settings(core.db, GUILD_ID)["custom"])
    settings = layout.level_settings(core.db, GUILD_ID)
    assert settings["announce"] is False and settings["leaderboard_day"] == 4
    assert [at for at, _r in layout.level_roles(core.db, GUILD_ID)] == [5, 20]
    wait_for_window(qapp, lambda: "Saved." in shown_text(page))


def test_a_players_stats_and_level_show_on_the_players_page(qapp, fake, discord_http):  # noqa: F811
    import json

    from agent.addons import world_path

    from .test_desktop_app import signed_in_on

    core = discord_http.agent.app.state.core
    uuid = "069a79f4-44e9-4726-a5be-fca90e38aaf5"
    core.db.execute(
        "INSERT INTO players (server_id, username, uuid, first_seen, last_seen, total_seconds, "
        "sessions) VALUES ('survival', 'Alex', ?, 1, 1, ?, 2)",
        (uuid, 20 * 3600),
    )
    core.links.link(uuid, "Alex", "java", "survival", MEMBER_ID, "sam", "t")
    stats = world_path(core.servers["survival"]) / "stats" / f"{uuid}.json"
    stats.parent.mkdir(parents=True, exist_ok=True)
    stats.write_text(
        json.dumps({"stats": {"minecraft:custom": {"minecraft:deaths": 2}}}), encoding="utf-8"
    )
    window = signed_in_on(qapp, discord_http)
    page = open_page(qapp, window, "Players and chat")
    wait_for_window(qapp, lambda: "Alex: sam on Discord  ·  Level 5 (200 XP)" in shown_text(page))
    stats_button = lambda: next(  # noqa: E731
        b for b in page.findChildren(QtWidgets.QPushButton) if b.accessibleName() == "Stats: Alex"
    )
    stats_button().click()
    wait_for_window(qapp, lambda: "Deaths 2  ·  Last saved" in shown_text(page))
    stats_button().click()
    wait_for_window(qapp, lambda: "Deaths 2" not in shown_text(page))
