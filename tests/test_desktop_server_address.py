"""The desktop app's Server address card: the ways in, "Let friends join
from the internet" with a fake router, the outside test and the next step
when the router or the internet provider says no."""

from __future__ import annotations

import pytest

from agent import downloads, internet, upnp, waysin

from .test_desktop_app import button, owner, shown_text, wait_for
from .test_desktop_settings import card_of, settings_page, switch_named
from .test_internet import FakeRouter

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")

TITLE = "Server address"
SWITCH = "Let friends join from the internet"


@pytest.fixture(autouse=True)
def fresh_states():
    internet._states.clear()
    yield
    internet._states.clear()


@pytest.fixture
def router(monkeypatch):
    fake = FakeRouter(public="1.2.3.4")

    async def find():
        return upnp.Router(fake, "192.168.1.1", "Home router")

    monkeypatch.setattr(upnp, "find_router", find)
    monkeypatch.setattr(upnp, "local_address_towards", lambda host: "192.168.1.20")
    return fake


def running(qapp, agent_http):
    headers = owner(agent_http)
    agent_http.post("/api/servers/survival/server/start", json={}, headers=headers)
    wait_for(
        qapp,
        lambda: (
            agent_http.get("/api/servers/survival/status", headers=headers).json()["state"]
            == "ONLINE"
        ),
        20,
        0.2,
    )


def test_off_by_default_with_what_it_would_do(qapp, agent_http):
    _window, page = settings_page(qapp, agent_http)
    frame = card_of(page, TITLE)
    wait_for(qapp, lambda: "asks your router to send port" in shown_text(frame))
    assert not switch_named(frame, SWITCH).isChecked()


def test_turned_on_shows_the_address_and_tests_it_once_allowed(
    qapp, agent_http, router, monkeypatch
):
    asked = []

    async def ask(address, bedrock, since):
        asked.append(address)
        return True

    monkeypatch.setattr(waysin, "ask_mcsrvstat", ask)
    running(qapp, agent_http)
    window, page = settings_page(qapp, agent_http)
    frame = card_of(page, TITLE)
    switch_named(frame, SWITCH).click()
    wait_for(qapp, lambda: "Internet address: 1.2.3.4:" in shown_text(frame), 20)
    assert "isn't known yet" in shown_text(frame)
    monkeypatch.setattr(window, "confirm", lambda *a, **k: True)
    button(frame, "Test from the internet").click()
    wait_for(
        qapp, lambda: "Friends can join from the internet at 1.2.3.4:" in shown_text(frame), 20
    )
    assert len(asked) == 1


def test_no_router_shows_the_port_forwarding_steps(qapp, agent_http, monkeypatch):
    async def none():
        raise upnp.UpnpError("no_router")

    monkeypatch.setattr(upnp, "find_router", none)
    running(qapp, agent_http)
    _window, page = settings_page(qapp, agent_http)
    frame = card_of(page, TITLE)
    switch_named(frame, SWITCH).click()
    wait_for(qapp, lambda: "No router answered" in shown_text(frame), 20)
    assert "forward TCP port" in shown_text(frame)


def test_the_providers_shared_address_offers_playit(qapp, agent_http, router):
    router.public = "100.72.1.2"
    running(qapp, agent_http)
    _window, page = settings_page(qapp, agent_http)
    frame = card_of(page, TITLE)
    switch_named(frame, SWITCH).click()
    wait_for(qapp, lambda: "shares one internet address" in shown_text(frame), 20)
    assert button(frame, "Use playit.gg")


def test_a_duckdns_name_is_saved_from_the_card_and_can_be_removed(qapp, agent_http, monkeypatch):
    from .test_ddns import TOKEN, FakeSites

    sites = FakeSites()
    monkeypatch.setattr(downloads, "fetch_bytes", sites.fetch)
    window, page = settings_page(qapp, agent_http)
    frame = card_of(page, TITLE)
    wait_for(qapp, lambda: "A name instead of numbers" in shown_text(frame))
    button(frame, "Use a DuckDNS name").click()
    boxes = {box.accessibleName(): box for box in frame.findChildren(QtWidgets.QLineEdit)}
    boxes["Your DuckDNS name"].setText("myserver")
    boxes["Token"].setText(TOKEN)
    button(frame, "Save").click()
    wait_for(qapp, lambda: "DuckDNS name: myserver.duckdns.org" in shown_text(frame), 20)
    assert sites.asked[0][1]["domains"] == "myserver"
    monkeypatch.setattr(window, "confirm", lambda *a, **k: True)
    button(frame, "Remove name").click()
    wait_for(qapp, lambda: "DuckDNS name:" not in shown_text(frame), 20)
    wait_for(qapp, lambda: "A name instead of numbers" in shown_text(frame), 5)
    assert button(frame, "Use a DuckDNS name")


def test_a_saved_noip_name_opens_noip_then_i_confirmed_it(qapp, agent_http, monkeypatch):
    from .test_ddns import FakeSites

    sites = FakeSites()
    monkeypatch.setattr(downloads, "fetch_bytes", sites.fetch)
    window, page = settings_page(qapp, agent_http)
    frame = card_of(page, TITLE)
    wait_for(qapp, lambda: "A name instead of numbers" in shown_text(frame))
    button(frame, "Use a No-IP name").click()
    boxes = {box.accessibleName(): box for box in frame.findChildren(QtWidgets.QLineEdit)}
    boxes["Your No-IP name, like myserver.ddns.net"].setText("mine.ddns.net")
    boxes["No-IP username"].setText("me")
    boxes["No-IP password"].setText("pw")
    button(frame, "Save").click()
    wait_for(qapp, lambda: "No-IP name: mine.ddns.net" in shown_text(frame), 20)
    wait_for(qapp, lambda: "Confirm it on noip.com before" in shown_text(frame), 5)
    opened = []
    monkeypatch.setattr(window.pc, "open_link", opened.append)
    button(frame, "Open noip.com").click()
    assert opened == ["https://my.noip.com/dynamic-dns"]
    assert button(frame, "I confirmed it")


def test_playit_installs_on_click_links_and_shows_the_tunnel(
    qapp, agent_http, monkeypatch, tmp_path
):
    from agent import playit

    from .test_playit import FakeProgram, running, tunnel

    fake = FakeProgram()
    fake.life = None  # no program yet, so no pipe
    exe = tmp_path / "playit.exe"
    monkeypatch.setattr(playit, "PROGRAM", exe)  # not there yet: not installed
    monkeypatch.setattr(playit, "ask", fake.ask)
    monkeypatch.setattr(downloads, "fetch_json", fake.fetch_json)
    window, page = settings_page(qapp, agent_http)
    frame = card_of(page, TITLE)
    wait_for(qapp, lambda: "without opening a port" in shown_text(frame))
    monkeypatch.setattr(window, "confirm", lambda *a, **k: True)

    def install() -> None:
        window.pc.playit_installs += 1
        exe.write_text("")
        fake.life = {"state": "waiting_for_secret"}

    monkeypatch.setattr(window.pc, "install_playit", install)
    button(frame, "Use playit.gg").click()
    wait_for(qapp, lambda: "approve this PC" in shown_text(frame), 20)
    assert window.pc.playit_installs == 1
    wait_for(qapp, lambda: any(r["type"] == "set_secret" for r in fake.asked), 20)
    button(frame, "Check again").click()
    wait_for(qapp, lambda: "Make a tunnel on playit.gg" in shown_text(frame), 20)
    port = internet.router_ports(agent_http.agent.app.state.core.servers["survival"])[0][1]
    fake.life = running(tunnel("cool-name.gl.joinmc.link", port))
    button(frame, "Check again").click()
    wait_for(qapp, lambda: "Friends join with: cool-name.gl.joinmc.link" in shown_text(frame), 20)
    # Stop, then the program is offered for removal (said yes here).
    button(frame, "Stop using playit.gg").click()
    wait_for(qapp, lambda: window.pc.playit_uninstalls == 1, 20)
    assert {"type": "reset_secret"} in fake.asked
