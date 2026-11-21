"""DuckDNS and No-IP names (agent/ddns.py), against fake sites.

Nothing on the real internet is asked: the one request each site gets is
replaced with a fake that answers like the site does. A name is only saved
after the site said yes, the secrets never come back in an answer, and No-IP
is left alone after an answer that says stop.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import pytest

from agent import ddns, downloads

from . import test_protection

agent = test_protection.agent  # the real agent, as a fixture

TOKEN = "0a1b2c3d-1111-2222-3333-444455556666"


class FakeSites:
    def __init__(self):
        self.asked: list[tuple[str, dict, dict]] = []
        self.duck = "OK\n5.6.7.8\n\nUPDATED"
        self.noip = "good 5.6.7.8"

    async def fetch(self, url, max_bytes=0, **request):
        parsed = urlparse(url)
        query = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        self.asked.append((parsed.hostname, query, request.get("headers") or {}))
        downloads.check_url(url)  # the hosts must be allowed
        return (self.duck if parsed.hostname == "www.duckdns.org" else self.noip).encode()


@pytest.fixture
def sites(monkeypatch):
    fake = FakeSites()
    monkeypatch.setattr(downloads, "fetch_bytes", fake.fetch)
    return fake


def names(client):
    return client.app.state.core.ddns


def test_nothing_saved_at_first(agent):
    body = agent.get("/api/ddns").json()
    assert body["duckdns"] is None and body["noip"] is None
    assert body["duckdns_site"].startswith("https://")


def test_duckdns_is_saved_only_after_it_said_ok_and_the_token_never_comes_back(agent, sites):
    body = agent.put("/api/ddns/duckdns", json={"name": "MyServer.duckdns.org", "token": TOKEN})
    assert body.status_code == 200, body.text
    assert body.json()["duckdns"]["name"] == "myserver.duckdns.org"
    assert body.json()["duckdns"]["ip"] == "5.6.7.8"
    host, query, _ = sites.asked[0]
    assert host == "www.duckdns.org" and query["domains"] == "myserver" and query["ip"] == ""
    assert TOKEN not in body.text and TOKEN not in agent.get("/api/ddns").text
    # Kept in the secrets file, not in the database's settings.
    assert TOKEN not in str(names(agent).saved())


def test_duckdns_saying_ko_saves_nothing(agent, sites):
    sites.duck = "KO"
    body = agent.put("/api/ddns/duckdns", json={"name": "myserver", "token": TOKEN})
    assert body.status_code == 400 and "DuckDNS said no" in body.json()["detail"]
    assert agent.get("/api/ddns").json()["duckdns"] is None


@pytest.mark.parametrize(
    ("name", "token"), [("my server", TOKEN), ("-bad", TOKEN), ("myserver", "not-a-token")]
)
def test_bad_duckdns_input_is_refused_before_asking(agent, sites, name, token):
    body = agent.put("/api/ddns/duckdns", json={"name": name, "token": token})
    assert body.status_code == 400 and sites.asked == []


def test_noip_sends_basic_auth_and_its_user_agent_and_counts_30_days(agent, sites):
    body = agent.put(
        "/api/ddns/noip",
        json={"name": "Mine.ddns.net", "username": "me", "password": "secret-pass"},
    )
    assert body.status_code == 200, body.text
    noip = body.json()["noip"]
    assert noip["name"] == "mine.ddns.net" and noip["confirm_soon"] is False
    assert noip["confirm_by"] == pytest.approx(time.time() + 30 * ddns.DAY, abs=60)
    host, query, headers = sites.asked[0]
    assert host == "dynupdate.no-ip.com" and query == {"hostname": "mine.ddns.net"}
    assert headers["Authorization"].startswith("Basic ")
    assert headers["User-Agent"].startswith("CommandBlock CommandBlock/")
    assert "secret-pass" not in body.text


@pytest.mark.parametrize("answer", ["badauth", "nohost", "abuse", "badagent", "!donator", "911"])
def test_noip_errors_save_nothing(agent, sites, answer):
    sites.noip = answer
    body = agent.put(
        "/api/ddns/noip", json={"name": "mine.ddns.net", "username": "me", "password": "pw"}
    )
    assert body.status_code == 400
    assert agent.get("/api/ddns").json()["noip"] is None


def test_only_the_owner_may_save_or_remove(agent, sites, monkeypatch):
    from agent.security.auth import Principal

    monkeypatch.setattr(Principal, "is_owner", property(lambda self: False))
    body = agent.put("/api/ddns/duckdns", json={"name": "myserver", "token": TOKEN})
    assert body.status_code == 403 and sites.asked == []
    assert agent.delete("/api/ddns/duckdns").status_code == 403


def test_remove_forgets_the_name_and_its_secret(agent, sites):
    agent.put("/api/ddns/duckdns", json={"name": "myserver", "token": TOKEN})
    body = agent.delete("/api/ddns/duckdns").json()
    assert body["duckdns"] is None
    assert "duckdns_token" not in ddns.read_secrets(names(agent).path)
    assert agent.delete("/api/ddns/other").status_code == 404


async def _tick(client):
    await names(client).tick()


def test_noip_is_only_sent_again_when_the_address_changed(agent, sites, monkeypatch):
    import asyncio

    agent.put("/api/ddns/noip", json={"name": "mine.ddns.net", "username": "me", "password": "pw"})
    asked = len(sites.asked)
    monkeypatch.setattr(ddns, "router_address", lambda core: "5.6.7.8")
    asyncio.run(_tick(agent))
    assert len(sites.asked) == asked  # same address: No-IP counts "nochg" against us
    monkeypatch.setattr(ddns, "router_address", lambda core: "9.9.9.9")
    asyncio.run(_tick(agent))
    assert sites.asked[-1][1] == {"hostname": "mine.ddns.net", "myip": "9.9.9.9"}


def test_noip_stops_after_an_answer_that_says_stop_and_alerts_once(agent, sites, monkeypatch):
    import asyncio

    agent.put("/api/ddns/noip", json={"name": "mine.ddns.net", "username": "me", "password": "pw"})
    published = []

    async def publish(event):
        published.append(event.type)

    monkeypatch.setattr(names(agent).core.bus, "publish", publish)
    sites.noip = "badauth"
    monkeypatch.setattr(ddns, "router_address", lambda core: "9.9.9.9")
    asyncio.run(_tick(agent))
    asked = len(sites.asked)
    monkeypatch.setattr(ddns, "router_address", lambda core: "8.8.8.8")
    asyncio.run(_tick(agent))
    assert len(sites.asked) == asked  # stopped until saved again
    assert published == ["ddns_failed"]
    noip = agent.get("/api/ddns").json()["noip"]
    assert "username or password" in noip["problem"]
    names_now = agent.get(f"{test_protection.BASE}/internet").json()["names"]
    assert names_now == []  # a stopped name isn't offered to friends


def test_the_confirm_alert_comes_a_week_before_once_and_i_confirmed_resets(
    agent, sites, monkeypatch
):
    import asyncio

    agent.put("/api/ddns/noip", json={"name": "mine.ddns.net", "username": "me", "password": "pw"})
    published = []

    async def publish(event):
        published.append(event.type)

    monkeypatch.setattr(names(agent).core.bus, "publish", publish)
    asyncio.run(names(agent).remind_confirm())
    assert published == []
    found = names(agent)
    noip = found.saved()["noip"]
    found._save("noip", {**noip, "confirmed_at": noip["confirmed_at"] - 24 * ddns.DAY})
    asyncio.run(names(agent).remind_confirm())
    asyncio.run(names(agent).remind_confirm())
    assert published == ["noip_confirm"]
    assert agent.get("/api/ddns").json()["noip"]["confirm_soon"] is True
    body = agent.post("/api/ddns/noip/confirmed").json()
    assert body["noip"]["confirm_soon"] is False


def test_duckdns_follows_the_routers_address(agent, sites, monkeypatch):
    import asyncio

    agent.put("/api/ddns/duckdns", json={"name": "myserver", "token": TOKEN})
    monkeypatch.setattr(ddns, "router_address", lambda core: "5.6.7.8")
    asyncio.run(_tick(agent))  # the first address the router said
    asked = len(sites.asked)
    asyncio.run(_tick(agent))
    assert len(sites.asked) == asked  # same address, not due yet
    monkeypatch.setattr(ddns, "router_address", lambda core: "9.9.9.9")
    asyncio.run(_tick(agent))
    assert len(sites.asked) == asked + 1
    names_now = agent.get(f"{test_protection.BASE}/internet").json()["names"]
    assert names_now == [{"provider": "duckdns", "name": "myserver.duckdns.org"}]


def test_duckdns_out_of_reach_is_tried_again_but_ko_stops_it(agent, sites, monkeypatch):
    import asyncio

    agent.put("/api/ddns/duckdns", json={"name": "myserver", "token": TOKEN})
    found = names(agent)

    async def offline(url, max_bytes=0, **request):
        raise downloads.DownloadError("Couldn't reach www.duckdns.org.")

    monkeypatch.setattr(downloads, "fetch_bytes", offline)
    monkeypatch.setattr(ddns, "router_address", lambda core: "9.9.9.9")
    asyncio.run(_tick(agent))
    assert found.saved()["duckdns"]["problem"] is None  # tried again later
    monkeypatch.setattr(downloads, "fetch_bytes", sites.fetch)
    monkeypatch.setattr(ddns, "router_address", lambda core: "8.8.8.8")
    sites.duck = "KO"
    monkeypatch.setattr(found.core.bus, "publish", _no_publish)
    asyncio.run(_tick(agent))
    assert "DuckDNS said no" in found.saved()["duckdns"]["problem"]


async def _no_publish(event):
    return None
