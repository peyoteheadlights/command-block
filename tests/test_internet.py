""" "Let friends join from the internet": the router port by UPnP and the
outside test.

The router is a fake with the same calls as async-upnp-client's IgdDevice,
so nothing on the real network is asked. Nothing is guessed: a port counts
as open only when the router lists it back, and the card says "not known"
until an outside test answered.
"""

from __future__ import annotations

from ipaddress import IPv4Address
from types import SimpleNamespace

import pytest

from agent import internet, protection, upnp, waysin

from . import test_protection
from .test_protection import BASE, ctx_of, start, wait_for

agent = test_protection.agent  # the real agent, as a fixture


class FakeRouter:
    """The calls agent/upnp.py makes on an IgdDevice."""

    def __init__(self, public="1.2.3.4", keeps=True):
        self.public = public
        self.keeps = keeps
        self.mapped: dict[tuple[str, int], str] = {}

    async def async_add_port_mapping(self, **kw):
        if self.keeps:
            self.mapped[(kw["protocol"], kw["external_port"])] = kw["internal_client"].exploded

    async def async_get_specific_port_mapping_entry(self, remote_host, external_port, protocol):
        client = self.mapped.get((protocol, external_port))
        return SimpleNamespace(internal_client=IPv4Address(client)) if client else None

    async def async_delete_port_mapping(self, remote_host, external_port, protocol):
        self.mapped.pop((protocol, external_port), None)

    async def async_get_external_ip_address(self):
        return self.public


@pytest.fixture(autouse=True)
def fresh_states():
    internet._states.clear()
    yield
    internet._states.clear()


@pytest.fixture
def router(monkeypatch):
    fake = FakeRouter()

    async def find():
        return upnp.Router(fake, "192.168.1.1", "Home router")

    monkeypatch.setattr(upnp, "find_router", find)
    monkeypatch.setattr(upnp, "local_address_towards", lambda host: "192.168.1.20")
    return fake


@pytest.fixture
def mcsrvstat(monkeypatch):
    asked = []

    async def ask(address, bedrock, since):
        asked.append(address)
        return True

    monkeypatch.setattr(waysin, "ask_mcsrvstat", ask)
    return asked


def switch(client, on, headers=None):
    response = client.put(f"{BASE}/internet", json={"on": on}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------ upnp.py
@pytest.mark.parametrize(
    ("address", "kind"),
    [
        ("1.2.3.4", "public"),
        ("100.72.1.2", "shared"),
        ("192.168.0.2", "private"),
        ("", "none"),
        (None, "none"),
    ],
)
def test_the_routers_address_is_sorted_by_what_it_is(address, kind):
    assert upnp.address_kind(address) == kind


async def test_a_port_counts_as_open_only_when_the_router_lists_it_back(router):
    opened = await upnp.open_ports([("TCP", 25565), ("UDP", 19132)], "Command Block Survival")
    assert router.mapped == {("TCP", 25565): "192.168.1.20", ("UDP", 19132): "192.168.1.20"}
    assert opened.public_address == "1.2.3.4" and opened.local_address == "192.168.1.20"
    await upnp.close_ports(opened.ports)
    assert router.mapped == {}


async def test_a_router_that_doesnt_keep_the_port_is_a_refusal(router):
    router.keeps = False
    with pytest.raises(upnp.UpnpError) as caught:
        await upnp.open_ports([("TCP", 25565)], "x")
    assert caught.value.reason == "refused"


# ------------------------------------------------------------ the API and the hooks
def test_the_protection_cards_router_hooks_are_upnps():
    assert protection.ROUTER.open is internet.open_port
    assert protection.ROUTER.close is internet.close_port


def test_off_by_default_and_never_claims_the_internet_works(agent):
    body = agent.get(f"{BASE}/internet").json()
    assert body["on"] is False and body["known"] is False and body["works"] is None
    assert body["reason"] == "off" and body["address"] is None


def test_turned_on_while_running_opens_tests_once_allowed_and_closes_on_off(
    agent, router, mcsrvstat
):
    start(agent)
    body = switch(agent, True)
    port = body["port"]
    assert router.mapped == {("TCP", port): "192.168.1.20"}
    assert body["address"] == "1.2.3.4" and body["local_address"] == "192.168.1.20"
    # mcsrvstat.us is asked only after the owner said yes once.
    assert mcsrvstat == [] and body["known"] is False and body["reason"] == "not_tested"
    refused = agent.post(f"{BASE}/internet/test", json={})
    assert refused.status_code == 409
    body = agent.post(f"{BASE}/internet/test", json={"allow_mcsrvstat": True}).json()
    assert mcsrvstat == [f"1.2.3.4:{port}"]
    assert body["known"] is True and body["works"] is True and body["tested_by"] == "mcsrvstat"
    assert body["mcsrvstat_ok"] is True
    # On the next start it tests by itself.
    stopped = agent.post(f"{BASE}/server/stop", json={})
    assert stopped.status_code == 200, stopped.text
    wait_for(lambda: router.mapped == {}, 20)
    start(agent)
    wait_for(lambda: len(mcsrvstat) == 2, 20)
    body = switch(agent, False)
    assert router.mapped == {} and body["reason"] == "off" and body["known"] is False


def test_the_phone_test_counts_only_for_the_address_on_the_card(agent, router):
    start(agent)
    port = switch(agent, True)["port"]
    wrong = agent.post(
        f"{BASE}/internet/phone-test", json={"address": "198.51.100.1", "port": port, "works": True}
    ).json()
    assert wrong["known"] is False
    body = agent.post(
        f"{BASE}/internet/phone-test", json={"address": "1.2.3.4", "port": port, "works": False}
    ).json()
    assert body["known"] is True and body["works"] is False and body["tested_by"] == "phone"


def test_a_shared_address_from_the_provider_offers_playit(agent, router):
    router.public = "100.72.1.2"
    start(agent)
    body = switch(agent, True)
    assert body["problem"] == "shared" and body["playit"] == internet.PLAYIT
    assert body["address"] is None and body["known"] is False


def test_no_router_leaves_the_steps_to_forward_by_hand(agent, monkeypatch):
    async def none():
        raise upnp.UpnpError("no_router", "No router answered UPnP on the home network.")

    monkeypatch.setattr(upnp, "find_router", none)
    start(agent)
    body = switch(agent, True)
    assert body["problem"] == "no_router" and body["reason"] == "no_router"
    assert body["ports"] == [{"protocol": "TCP", "port": body["port"]}]


def test_only_the_owner_opens_the_router_port(agent):
    from agent.security import permissions as P

    from .test_helpers import add_helper
    from .test_moderators import give

    owner = dict(agent.headers)
    sam = add_helper(agent, owner)
    give(agent, owner, {"survival": P.RUN_IT})
    response = agent.put(f"{BASE}/internet", json={"on": True}, headers=sam)
    assert response.status_code == 403
    assert ctx_of(agent).config.server.internet is False
