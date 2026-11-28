"""playit.gg built in (agent/playit.py), with a pretend playit program.

Nothing real is asked: playit's pipe and its two sign-in calls are replaced
by fakes that answer like playit-agent 2.0.2 does. The program's key goes
straight to the program and never comes back in an answer, a tunnel only
counts for the server whose port it sends to, and Stop unlinks the program.
"""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from agent import downloads, internet, playit, waysin
from agent.discord import layout

from . import test_protection
from .test_protection import BASE, ctx_of, wait_for

agent = test_protection.agent  # the real agent, as a fixture

KEY = "a1b2c3-the-programs-key"


def running(*tunnels):
    data = {"tunnels": list(tunnels), "login_link": None, "account_status": "verified"}
    return {"state": "running", "data": data}


def tunnel(address, port, disabled=None):
    return {
        "id": "t1",
        "display_address": address,
        "destination": f"127.0.0.1:{port}",
        "is_disabled": disabled is not None,
        "disabled_reason": disabled,
    }


class FakeProgram:
    """playit's program: its pipe and playit.gg's claim calls."""

    def __init__(self):
        self.life: dict | None = {"state": "waiting_for_secret"}  # None: not running
        self.asked: list[dict] = []
        self.claims: list[tuple[str, dict]] = []
        self.accept = "UserAccepted"
        self.tunnels: list[dict] = []

    def ask(self, request):
        self.asked.append(request)
        if self.life is None:
            raise FileNotFoundError("no pipe")
        kind = request["type"]
        if kind == "get_state":
            return {"type": "state", "data": self.life}
        if kind == "set_secret":
            self.life = running(*self.tunnels)
            return {"type": "set_secret", "data": {"accepted": True}}
        if kind == "reset_secret":
            self.life = {"state": "waiting_for_secret"}
            return {"type": "reset_secret", "data": {"accepted": True}}
        raise AssertionError(kind)

    async def fetch_json(self, url, max_bytes=0, **request):
        downloads.check_url(url)  # the host and path must be allowed
        body = json.loads(request["content"])
        self.claims.append((url, body))
        if url.endswith("/claim/setup"):
            # The first answer: the owner hasn't opened the page yet.
            first = len(self.claims) == 1
            return {"status": "success", "data": "WaitingForUserVisit" if first else self.accept}
        return {"status": "success", "data": {"secret_key": KEY}}


@pytest.fixture
def program(monkeypatch, tmp_path):
    fake = FakeProgram()
    exe = tmp_path / "playit.exe"
    exe.write_text("")
    monkeypatch.setattr(playit, "PROGRAM", exe)
    monkeypatch.setattr(playit, "ask", fake.ask)
    monkeypatch.setattr(downloads, "fetch_json", fake.fetch_json)
    internet._states.clear()
    return fake


def core(client):
    return client.app.state.core


def port_of(client, server_id="survival"):
    return internet.router_ports(ctx_of(client, server_id))[0][1]


def test_the_pipe_talk_skips_events_and_repeats_the_programs_version():
    sent = io.BytesIO()

    class Pipe:
        lines = [
            b'{"message_kind":"hello","data":{"protocol":{"ipc_version":7,"capabilities":[]}}}\n',
            b'{"message_kind":"event","data":{"ipc_version":7,"event":{"type":"log"}}}\n'
            b'{"message_kind":"response","data":{"ipc_version":7,"request_id":1,',
            b'"response":{"type":"state","data":{"state":"starting"}}}}\n',
        ]

        def read(self, size):
            return self.lines.pop(0) if self.lines else b""

        def write(self, data):
            sent.write(data)

    answer = playit.talk(Pipe(), {"type": "get_state"})
    assert answer == {"type": "state", "data": {"state": "starting"}}
    assert json.loads(sent.getvalue()) == {
        "ipc_version": 7,
        "request_id": 1,
        "request": {"type": "get_state"},
    }


def test_not_installed_and_not_running_are_told_apart(agent, program, monkeypatch, tmp_path):
    program.life = None
    assert agent.get(f"{BASE}/playit").json()["phase"] == "stopped"
    monkeypatch.setattr(playit, "PROGRAM", tmp_path / "missing.exe")
    body = agent.get(f"{BASE}/playit").json()
    assert body["phase"] == "not_installed" and body["on"] is False
    assert agent.post(f"{BASE}/playit/use", json={}).status_code == 409


def test_use_links_the_program_and_the_key_goes_only_to_the_program(agent, program):
    body = agent.post(f"{BASE}/playit/use", json={})
    assert body.status_code == 200, body.text
    claim = body.json()["claim"]
    assert claim["state"] == "waiting" and claim["url"].startswith("https://playit.gg/claim/")
    code = claim["url"].rsplit("/", 1)[1]
    wait_for(lambda: {"type": "set_secret", "secret": KEY} in program.asked, 10)
    assert (
        program.claims[0][1]["code"] == code and program.claims[0][1]["agent_type"] == "assignable"
    )
    body = agent.get(f"{BASE}/playit")
    assert body.json()["phase"] == "running" and body.json()["claim"] is None
    assert KEY not in body.text and KEY not in json.dumps(core(agent).playit.saved())


def test_a_rejected_link_says_so(agent, program):
    program.accept = "UserRejected"
    agent.post(f"{BASE}/playit/use", json={})
    wait_for(lambda: (core(agent).playit.claim or {}).get("state") == "rejected", 10)
    assert not any(r["type"] == "set_secret" for r in program.asked)


def test_a_tunnel_counts_only_for_the_server_on_its_port_and_fills_the_shared_address(
    agent, program
):
    port = port_of(agent)
    program.tunnels = [tunnel("cool-name.gl.joinmc.link", port), tunnel("other.ply.gg:5", 1)]
    program.life = running(*program.tunnels)
    agent.post(f"{BASE}/playit/use", json={})
    body = agent.get(f"{BASE}/playit").json()
    assert body["proxy"] in ("v2", "none") and body["claim"] is None
    (java,) = body["ways"]
    assert java["kind"] == "java" and java["local_port"] == port
    assert java["tunnel"]["address"] == "cool-name.gl.joinmc.link"
    assert layout.addresses(core(agent).db)["survival"] == "cool-name.gl.joinmc.link"
    other = agent.get("/api/servers/creative/playit").json()
    assert other["ways"][0]["tunnel"] is None
    assert "creative" not in layout.addresses(core(agent).db)
    # Stop unlinks the program and takes back only the address it filled in.
    layout.set_address(core(agent).db, "creative", "typed.example.com")
    body = agent.post(f"{BASE}/playit/stop", json={}).json()
    assert {"type": "reset_secret"} in program.asked
    assert body["on"] is False and body["phase"] == "waiting_for_secret"
    assert layout.addresses(core(agent).db) == {"creative": "typed.example.com"}


def test_an_address_the_owner_typed_is_never_replaced(agent, program):
    layout.set_address(core(agent).db, "survival", "mine.example.com")
    program.life = running(tunnel("cool-name.gl.joinmc.link", port_of(agent)))
    agent.post(f"{BASE}/playit/use", json={})
    assert layout.addresses(core(agent).db)["survival"] == "mine.example.com"


def test_one_alert_when_it_stops_and_reconnect_runs_playit_start(agent, program, monkeypatch):
    program.life = running()
    agent.post(f"{BASE}/playit/use", json={})
    published = []

    async def publish(event):
        published.append(event.type)

    monkeypatch.setattr(core(agent).bus, "publish", publish)
    program.life = {"state": "error", "data": {"message": "playit is down"}}
    asyncio.run(core(agent).playit.tick())
    asyncio.run(core(agent).playit.tick())
    assert published == ["playit_stopped"]
    body = agent.get(f"{BASE}/playit").json()
    assert body["down"] is True and body["problem"] == "playit is down"
    ran = []

    def run(command, **kw):
        ran.append(command)
        program.life = running()
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(playit.subprocess, "run", run)
    body = agent.post(f"{BASE}/playit/reconnect", json={}).json()
    assert ran == [[str(playit.PROGRAM), "start"]]
    assert body["down"] is False and body["phase"] == "running"


def test_the_outside_test_asks_mcsrvstat_only_after_yes(agent, program, monkeypatch):
    asked = []

    async def ask(address, bedrock, since):
        asked.append(address)
        return True

    monkeypatch.setattr(waysin, "ask_mcsrvstat", ask)
    program.life = running(tunnel("cool-name.gl.joinmc.link", port_of(agent)))
    agent.post(f"{BASE}/playit/use", json={})
    assert agent.post(f"{BASE}/playit-test", json={}).status_code == 409
    body = agent.post(f"{BASE}/playit-test", json={"allow_mcsrvstat": True}).json()
    assert asked == ["cool-name.gl.joinmc.link"]
    assert body["test"]["works"] is True and body["test"]["by"] == "mcsrvstat"


def test_only_the_owner_uses_or_stops_playit(agent, program, monkeypatch):
    from agent.security.auth import Principal

    monkeypatch.setattr(Principal, "is_owner", property(lambda self: False))
    for action in ("use", "reconnect", "stop"):
        assert agent.post(f"{BASE}/playit/{action}", json={}).status_code == 403
    assert program.asked == [] and program.claims == []


def test_a_moderator_never_gets_the_claim_page_or_a_login_link(agent, program, monkeypatch):
    from agent.security.auth import Principal

    program.accept = "WaitingForUser"  # the owner hasn't approved yet
    agent.post(f"{BASE}/playit/use", json={})
    monkeypatch.setattr(Principal, "is_owner", property(lambda self: False))
    body = agent.get(f"{BASE}/playit").json()
    assert body["claim"]["state"] == "waiting" and body["claim"]["url"] == ""
    assert body["new_tunnel"] == playit.NEW_TUNNEL
