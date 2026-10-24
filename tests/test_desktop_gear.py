"""The desktop app's gear (PC settings, Updates, Moderators, Signed-in
devices, Your account) and "?" (Getting started, About), driven
like a person would against the real agent."""

from __future__ import annotations

import zipfile

import pytest

from .fake_pc import FakePC
from .test_desktop_app import (
    button,
    on_all_servers,
    open_window,
    owner,
    shown_text,
    sign_in,
    tailscale_is,
    tailscale_ready,
    wait_for,
)

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


def gear(qapp, agent_http, section, pc=None, user=("admin", None)):
    window = open_window(qapp, agent_http, pc)
    if user[1]:
        sign_in(qapp, window, *user)
        wait_for(qapp, lambda: window.screen_name == "All servers" and window.role == "helper")
    else:
        sign_in(qapp, window)
        on_all_servers(qapp, window)
        wait_for(qapp, lambda: window.role == "owner")
    window.tools["gear"].click()
    window.open_list("PC settings", section)
    return window, window.gear.sections[section]


def shows(qapp, page, words, timeout=15):
    """Waits for a button with these words to show."""
    wait_for(qapp, lambda: words in shown_buttons(page), timeout)


def shown_buttons(page) -> list[str]:
    return [b.text() for b in page.findChildren(QtWidgets.QAbstractButton) if b.isVisibleTo(page)]


def listed(window) -> list[str]:
    sections = window.gear.list
    return [
        sections.item(i).text() for i in range(sections.count()) if not sections.item(i).isHidden()
    ]


def settings_of(agent_http) -> dict:
    return agent_http.get("/api/settings", headers=owner(agent_http)).json()["config"]


def add_moderator(agent_http, name="sam", password="moderator password", **more):
    made = agent_http.post(
        "/api/accounts",
        json={"username": name, "password": password, "servers": ["survival"], **more},
        headers=owner(agent_http),
    )
    assert made.status_code == 200, made.text
    return made.json()


# ------------------------------------------------------------ the list
def test_the_gear_lists_five_sections_for_the_owner_and_two_for_a_moderator(qapp, agent_http):
    window, _pc = gear(qapp, agent_http, "pc")
    assert window.screen_name == "PC settings"
    assert listed(window) == [
        "PC settings",
        "Updates",
        "Moderators",
        "Signed-in devices",
        "Your account",
    ]
    add_moderator(agent_http)
    window, _account = gear(qapp, agent_http, "pc", user=("sam", "moderator password"))
    assert listed(window) == ["Signed-in devices", "Your account"]
    assert window.gear.section == "devices"  # PC settings isn't theirs


def test_the_gear_opens_on_the_section_seen_last(qapp, agent_http):
    pc = FakePC()
    window, _ = gear(qapp, agent_http, "updates", pc)
    window.show_screen("All servers")
    window.tools["gear"].click()
    assert window.gear.section == "updates"


# ------------------------------------------------------------ PC settings
def test_the_look_is_kept_on_this_pc(qapp, agent_http):
    pc = FakePC()
    window, page = gear(qapp, agent_http, "pc", pc)
    dark = next(b for b in page.findChildren(QtWidgets.QRadioButton) if b.text() == "Dark")
    dark.click()
    assert pc.settings["look"] == "dark" and window.dark
    assert open_window(qapp, agent_http, pc, dark=None).dark  # next time too


def test_alerts_save_only_what_changed(qapp, agent_http):
    _window, page = gear(qapp, agent_http, "pc")
    shows(qapp, page, "Save alerts")
    button(page, "Save alerts").click()
    assert "Nothing changed." in shown_text(page)
    discord = next(b for b in page.findChildren(QtWidgets.QCheckBox) if b.text() == "Discord")
    discord.setChecked(not discord.isChecked())
    button(page, "Save alerts").click()
    wait_for(qapp, lambda: "Settings saved." in shown_text(page))
    assert settings_of(agent_http)["notifications"]["discord_enabled"] == discord.isChecked()
    assert "Add the Discord link to .env on the PC first." in shown_text(page)


def test_maintenance_mode_turns_on_from_its_switch(qapp, agent_http):
    _window, page = gear(qapp, agent_http, "pc")
    label = "Pause auto restarts and schedules on all servers"
    shows(qapp, page, label)
    next(b for b in page.findChildren(QtWidgets.QCheckBox) if b.text() == label).click()
    wait_for(qapp, lambda: "Maintenance mode is on." in shown_text(page))
    assert settings_of(agent_http)["maintenance"]["enabled"] is True


def test_moving_to_a_new_pc_saves_the_export_where_chosen(qapp, agent_http, tmp_path):
    pc = FakePC()
    pc.save_to = str(tmp_path / "moving.zip")
    _window, page = gear(qapp, agent_http, "pc", pc)
    shows(qapp, page, "Export everything")
    button(page, "Export everything").click()
    wait_for(qapp, lambda: "Saved to" in shown_text(page), 60)
    assert zipfile.is_zipfile(pc.save_to)


def test_the_startup_check_says_what_it_found(qapp, agent_http):
    _window, page = gear(qapp, agent_http, "pc")
    shows(qapp, page, "Check now")
    button(page, "Check now").click()
    wait_for(
        qapp, lambda: "Startup entry" in shown_text(page) or "Windows only" in shown_text(page)
    )


def test_use_a_different_account_runs_from_the_tailscale_card(qapp, agent_http, monkeypatch):
    tailscale_ready(agent_http, monkeypatch)
    tailscale_is(monkeypatch, connected=True)
    pc = FakePC()
    window, page = gear(qapp, agent_http, "pc", pc)
    wait_for(qapp, lambda: "Signed in as jo@example.com" in shown_text(page))
    tailscale_is(monkeypatch, connected=False)
    button(page, "Use a different account").click()
    assert window.screen_name == "Tailscale"
    wait_for(qapp, lambda: pc.tailscale_logouts == 1 and pc.tailscale_ups == 1)
    button(page, "Open Tailscale's Share page").click()
    assert pc.opened[-1].startswith("https://login.tailscale.com/admin/")


# ------------------------------------------------------------ Updates
def test_updates_says_which_version_this_is(qapp, agent_http):
    from agent import __version__

    _window, page = gear(qapp, agent_http, "updates")
    wait_for(qapp, lambda: __version__ in shown_text(page))
    assert "Check now" in [b.text() for b in page.findChildren(QtWidgets.QPushButton)]


# ------------------------------------------------------------ Moderators
def test_adding_a_moderator_shows_the_invite_once_with_the_share_link(
    qapp, agent_http, monkeypatch
):
    tailscale_ready(agent_http, monkeypatch)  # an invite needs the pairing link
    tailscale_is(monkeypatch, connected=True)
    _window, page = gear(qapp, agent_http, "moderators")
    wait_for(qapp, lambda: "Their name" in shown_text(page))
    page.findChild(QtWidgets.QLineEdit, "moderatorName").setText("alex")
    button(page, "Add moderator").click()
    wait_for(qapp, lambda: "Invite alex" in shown_text(page), 20)
    page.findChild(QtWidgets.QLineEdit, "shareLink").setText("https://login.tailscale.com/s/abc")
    button(page, "Copy invite").click()
    message = QtWidgets.QApplication.clipboard().text()
    assert "invite=" in message and "https://login.tailscale.com/s/abc" in message
    accounts = agent_http.get("/api/accounts", headers=owner(agent_http)).json()["helpers"]
    assert [a["username"] for a in accounts] == ["alex"] and accounts[0]["invite_waiting"]


def test_a_level_and_a_switch_are_saved_for_one_server(qapp, agent_http):
    add_moderator(agent_http)
    window, page = gear(qapp, agent_http, "moderators")
    window.set_mode(True)  # Make ops is a Technical switch
    window.open_list("PC settings", "moderators")
    wait_for(qapp, lambda: "sam" in shown_text(page))
    levels = next(
        c for c in page.findChildren(QtWidgets.QComboBox) if c.accessibleName().endswith("Survival")
    )
    levels.activated.emit(levels.findData("start_and_stop"))
    wait_for(qapp, lambda: "Saved" in shown_text(page), 20)

    def held() -> list[str]:
        sam = agent_http.get("/api/accounts", headers=owner(agent_http)).json()["helpers"][0]
        return next(a for a in sam["access"] if a["id"] == "survival")["permissions"]

    assert set(held()) == {"server.view", "server.control"}
    # Technical words: the permissions by their names.
    shows(qapp, page, "Edit permissions")
    button(page, "Edit permissions").click()

    def tick(words):
        return next(
            b
            for b in page.findChildren(QtWidgets.QCheckBox)
            if b.text() == words and b.isVisibleTo(page)
        )

    tick("players.op").click()
    assert tick("players.manage").isChecked()  # Make ops needs Players
    assert "Grants unrestricted in-game control." in shown_text(page)
    button(page, "Save for this server").click()
    wait_for(qapp, lambda: "players.op" in held(), 20)
    assert "players.manage" in held()


def test_removing_a_moderator_asks_first(qapp, agent_http):
    add_moderator(agent_http)
    window, page = gear(qapp, agent_http, "moderators")
    shows(qapp, page, "Remove moderator")
    window.confirm = lambda *words: False
    button(page, "Remove moderator").click()
    window.confirm = lambda *words: True
    button(page, "Remove moderator").click()
    wait_for(qapp, lambda: "sam was removed." in shown_text(page), 20)
    assert agent_http.get("/api/accounts", headers=owner(agent_http)).json()["helpers"] == []


# ------------------------------------------------------------ Signed-in devices, Your account
def test_signed_in_devices_signs_another_device_out_and_shows_the_pairing_link(qapp, agent_http):
    headers = owner(agent_http)  # another sign-in, like a phone
    window, page = gear(qapp, agent_http, "devices")
    wait_for(
        qapp,
        lambda: (
            "This device" in shown_text(page) and "Add a phone or another PC" in shown_text(page)
        ),
    )
    window.confirm = lambda *words: True
    outs = [b for b in page.findChildren(QtWidgets.QPushButton) if b.text() == "Sign out"]
    assert len(outs) == 2
    current = next(i for i, b in enumerate(outs) if "Desktop app" in b.accessibleName())
    outs[1 - current].click()
    wait_for(qapp, lambda: agent_http.get("/api/servers", headers=headers).status_code == 401, 20)
    assert window.screen_name == "PC settings"


def test_a_moderator_changes_their_own_password(qapp, agent_http):
    add_moderator(agent_http)
    _window, page = gear(qapp, agent_http, "account", user=("sam", "moderator password"))
    wait_for(qapp, lambda: "You're signed in as sam, a moderator." in shown_text(page))
    current, new, again = page.findChildren(QtWidgets.QLineEdit)
    current.setText("moderator password")
    new.setText("a brand new password")
    again.setText("a different one")
    button(page, "Change password").click()
    assert "The two passwords aren't the same." in shown_text(page)
    again.setText("a brand new password")
    button(page, "Change password").click()
    wait_for(qapp, lambda: "Password changed." in shown_text(page), 20)
    signed = agent_http.post(
        "/api/auth/login", json={"username": "sam", "password": "a brand new password"}
    )
    assert signed.status_code == 200


def test_an_alert_about_moderators_opens_that_section(qapp, agent_http):
    window, _ = gear(qapp, agent_http, "pc")
    window.open_alert({"page": "helpers", "server_id": None})
    assert window.screen_name == "PC settings" and window.gear.section == "moderators"


# ------------------------------------------------------------ "?"
def test_getting_started_buttons_open_their_pages(qapp, agent_http):
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    window.tools["help"].click()
    window.open_list("Help", "guide")
    guide = window.help.sections["guide"]
    wait_for(qapp, lambda: "1. Start your server" in shown_text(guide))
    button(guide, "Open Backups").click()
    wait_for(qapp, lambda: window.screen_name == "Server")
    assert window.server_screen.current_page == "Backups"
    window.tools["help"].click()
    window.open_list("Help", "guide")
    shows(qapp, guide, "Open Moderators")
    button(guide, "Open Moderators").click()
    assert window.screen_name == "PC settings" and window.gear.section == "moderators"


def test_about_has_the_version_licences_and_disclaimer(qapp, agent_http):
    from agent import __version__

    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    window.open_list("Help", "about")
    about = window.help.sections["about"]
    wait_for(qapp, lambda: "Qt for Python" in shown_text(about))
    words = shown_text(about)
    assert f"Version {__version__}" in words
    assert "NOT AN OFFICIAL MINECRAFT PRODUCT" in words
    assert "Qt for Python" in words
    button(about, "Full help online").click()
    assert pc.opened[-1].startswith("https://github.com/")


def test_about_has_support_the_project_and_the_two_forms(qapp, agent_http):
    import json

    from desktop.theme import SHARED

    links = json.loads((SHARED / "links.json").read_text(encoding="utf-8"))
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    window.open_list("Help", "about")
    about = window.help.sections["about"]
    wait_for(qapp, lambda: "Support the project" in shown_text(about))
    assert "unlocks nothing" in shown_text(about)
    for words, link in [
        ("GitHub Sponsors", "sponsors"),
        ("Ko-fi", "kofi"),
        ("Report a problem", "report"),
        ("Suggest a feature", "suggest"),
    ]:
        button(about, words).click()
        assert pc.opened[-1] == links[link]


# ------------------------------------------------------------ alerts per device
def checkbox(page, words):
    return next(b for b in page.findChildren(QtWidgets.QCheckBox) if b.text() == words)


def test_this_device_can_choose_its_own_alerts(qapp, agent_http):
    from .test_desktop_app import run_on_agent, server_state

    pc = FakePC()
    window, page = gear(qapp, agent_http, "account", pc)
    shows(qapp, page, "Save alerts")
    checkbox(page, "Choose alerts for this device").setChecked(True)
    checkbox(page, "Server started").setChecked(False)
    button(page, "Save alerts").click()
    wait_for(qapp, lambda: "Settings saved." in shown_text(page))
    headers = owner(agent_http)
    # The owner's other sign-in still follows the shared list.
    assert agent_http.get("/api/account/alerts", headers=headers).json()["own"] is False
    run_on_agent(agent_http, "survival", "start", headers)
    wait_for(qapp, lambda: server_state(agent_http, "survival", headers) == "ONLINE", 30, 0.2)
    run_on_agent(agent_http, "survival", "stop", headers)
    wait_for(qapp, lambda: any("stopped" in title for title, _body in pc.alerts), 30)
    assert not any("online" in title for title, _body in pc.alerts)


def test_alerts_can_be_kept_off_this_pc_while_the_bell_counts_them(qapp, agent_http):
    from .test_desktop_app import bell, run_on_agent

    pc = FakePC()
    window, page = gear(qapp, agent_http, "account", pc)
    shows(qapp, page, "Save alerts")
    checkbox(page, "Show alerts on this PC").setChecked(False)
    assert pc.settings["show_alerts"] is False
    run_on_agent(agent_http, "survival", "start", owner(agent_http))
    wait_for(qapp, lambda: bell(window) == "1", 30)
    assert pc.alerts == []


def test_start_the_agent_is_a_technical_switch_for_the_whole_pc(qapp, agent_http):
    add_moderator(agent_http)
    window, page = gear(qapp, agent_http, "moderators")
    wait_for(qapp, lambda: "sam" in shown_text(page))
    words = "agent.start: may wake the PC (Wake-on-LAN)"

    def ticks() -> list[str]:
        return [b.text() for b in page.findChildren(QtWidgets.QCheckBox) if b.isVisibleTo(page)]

    assert not any(t.startswith("Start the agent") for t in ticks())  # Simple: not offered
    window.set_mode(True)
    window.open_list("PC settings", "moderators")
    wait_for(qapp, lambda: words in ticks())
    next(b for b in page.findChildren(QtWidgets.QCheckBox) if b.text() == words).click()

    def pc() -> list[str]:
        return agent_http.get("/api/accounts", headers=owner(agent_http)).json()["helpers"][0]["pc"]

    wait_for(qapp, lambda: pc() == ["agent.start"], 20)


def test_a_saved_tailscale_key_makes_the_invites_share_link(qapp, agent_http, monkeypatch):
    from .test_tailscale_share import TOKEN, mock_tailscale_api

    tailscale_ready(agent_http, monkeypatch)
    tailscale_is(monkeypatch, connected=True)
    mock_tailscale_api(monkeypatch)
    window, page = gear(qapp, agent_http, "moderators")
    window.set_mode(True)
    window.open_list("PC settings", "moderators")
    shows(qapp, page, "Save key")
    next(
        b for b in page.findChildren(QtWidgets.QRadioButton) if b.text().startswith("API access")
    ).click()
    page.findChild(QtWidgets.QLineEdit, "shareSecret").setText(TOKEN)
    button(page, "Save key").click()
    wait_for(qapp, lambda: "API access token saved." in shown_text(page))
    page.findChild(QtWidgets.QLineEdit, "moderatorName").setText("alex")
    button(page, "Add moderator").click()
    shows(qapp, page, "Make share link (API)", 20)
    button(page, "Make share link (API)").click()
    share = page.findChild(QtWidgets.QLineEdit, "shareLink")
    wait_for(qapp, lambda: share.text() == "https://login.tailscale.com/admin/invite/abc")
