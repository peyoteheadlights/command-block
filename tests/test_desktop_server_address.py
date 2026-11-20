"""The desktop app's Server address card: the ways in, "Let friends join
from the internet" with a fake router, the outside test and the next step
when the router or the internet provider says no."""

from __future__ import annotations

import pytest

from agent import internet, upnp, waysin

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
