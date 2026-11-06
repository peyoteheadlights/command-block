"""Rented servers in Discord: only /rented status, start and stop, only when
the owner put the server on Discord, against the fake Discord and the fake
Pterodactyl panel."""

from __future__ import annotations

import httpx
import pytest

from agent import downloads, rented
from tests.fake_discord import GUILD_ID, wait_for
from tests.fake_panel import ADDRESS, KEY, FakePanel
from tests.test_discord import (  # noqa: F401  (agent and fake are fixtures)
    MOD_ROLE_OF_OWNER,
    agent,
    core_of,
    fake,
    run,
    set_up,
)


@pytest.fixture
def panel(monkeypatch):
    found = FakePanel()
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(found.handle))
    monkeypatch.setattr(rented, "CONNECT", found.connect)
    return found


def add(agent):  # noqa: F811
    body = {"address": ADDRESS, "key": KEY, "allow_host": True, "servers": ["abcd1234"]}
    answer = agent.post("/api/rented/panels", json=body)
    assert answer.status_code == 200, answer.text
    return answer.json()["added"][0]["id"]


def put_on_discord(agent, sid, choice):  # noqa: F811
    answer = agent.put(f"/api/rented/{sid}/discord", json={"choice": choice})
    assert answer.status_code == 200, answer.text


def test_rented_is_registered_only_once_a_server_is_on_discord(agent, fake, panel):  # noqa: F811
    set_up(agent)
    sid = add(agent)
    registered = wait_for(lambda: fake.made("PUT", "/applications/"))
    assert "rented" not in {c["name"] for c in registered[-1]}
    put_on_discord(agent, sid, "status")
    newest = wait_for(
        lambda: [r for r in fake.made("PUT", "/applications/") if "rented" in str(r)]
    )[-1]
    group = next(c for c in newest if c["name"] == "rented")
    assert {o["name"] for o in group["options"]} == {"status", "start", "stop"}
    assert group["options"][0]["options"][0]["choices"] == [
        {"name": "Survival (panel.example.com)", "value": sid}
    ]


def test_status_start_and_stop_follow_the_owners_choice(agent, fake, panel):  # noqa: F811
    set_up(agent)
    sid = add(agent)
    assert run(fake, "rented status", {"rented": sid}) == ["That rented server isn't on Discord."]
    put_on_discord(agent, sid, "status")
    assert run(fake, "rented status", {"rented": sid}) == ["Survival is stopped."]
    # The owner's role may start servers, but only status is allowed here.
    agent.put(
        f"/api/discord/guilds/{GUILD_ID}/roles/{MOD_ROLE_OF_OWNER}",
        json={"role_name": "Friends of Sam", "grants": {"test": ["server.control"]}},
    )
    words = run(fake, "rented start", {"rented": sid}, roles=[MOD_ROLE_OF_OWNER])[-1]
    assert "can't be started or stopped from Discord" in words
    put_on_discord(agent, sid, "control")
    words = run(fake, "rented start", {"rented": sid}, roles=[MOD_ROLE_OF_OWNER])[-1]
    assert words.startswith("Asked the host to start Survival")
    assert ("POST", "/servers/abcd1234/power", {"signal": "start"}) in panel.calls


def test_start_needs_a_role_that_may_start_servers(agent, fake, panel):  # noqa: F811
    set_up(agent)
    sid = add(agent)
    put_on_discord(agent, sid, "control")
    words = run(fake, "rented start", {"rented": sid})[-1]
    assert "roles don't allow" in words
    assert not [c for c in panel.calls if c[1].endswith("/power")]


def test_unknown_status_is_said_as_unknown(agent, fake, panel):  # noqa: F811
    set_up(agent)
    sid = add(agent)
    put_on_discord(agent, sid, "status")
    panel.down = True
    agent.portal.call(core_of(agent).rented.poll_all)
    words = run(fake, "rented status", {"rented": sid})[-1]
    assert "unknown" in words
