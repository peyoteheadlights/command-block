"""Diagnose server's other ways in (agent/waysin.py): home network, Tailscale,
the owner's other PC and the internet (mcsrvstat.us), and the changes they
lead to (Tailscale back on, the firewall rule, the router step)."""

# ruff: noqa: F811
# (pytest fixtures from tests/test_diagnose.py: "core" is used by name)
from __future__ import annotations

import time

import pytest

from agent import diagnose, firewall, joininfo, tailscale, waysin
from agent.discord import layout
from agent.monitoring import answering

from .test_diagnose import ALL_OK, core, diagnose_round, offline_mode, stand_in  # noqa: F401

HOME = "192.168.1.5"
TS = "100.101.102.103"
OTHER_PC = {"key": "pc-2", "body": {"name": "Laptop"}}


@pytest.fixture
def ways(core, monkeypatch, tmp_path):
    """A server with the test player joining fine, and every way in faked:
    ``state`` says what answers from where."""
    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    stand_in(monkeypatch, tmp_path, ALL_OK)
    ctx = core.get_server("survival")
    offline_mode(ctx)
    state = {
        "here": True,
        "there": None,  # None: no other PC
        "tailscale": None,  # None: not installed, else BackendState
        "internet": None,  # mcsrvstat's "online"
        "calls": [],
    }
    monkeypatch.setattr(
        joininfo,
        "local_addresses",
        lambda: [{"address": HOME, "adapter": "Ethernet", "virtual": False}],
    )

    def ping(protocol, host, port):
        return {"connected": state["here"], "answer": {"version": "x"} if state["here"] else None}

    monkeypatch.setattr(answering, "ping", ping)

    def status():
        found = state["tailscale"]
        return {
            "cli_found": found is not None,
            "backend_state": found,
            "connected": found == "Running",
            "addresses": [TS, "fd7a::1"] if found == "Running" else [],
            "error": None,
        }

    monkeypatch.setattr(tailscale, "tailscale_status", status)
    monkeypatch.setattr(tailscale, "switch", lambda on: state["calls"].append(f"tailscale {on}"))
    monkeypatch.setattr(core.pcs, "joined", lambda: state["there"] is not None)
    monkeypatch.setattr(core.pcs, "peers", lambda: [OTHER_PC])

    async def ping_from(peer, protocol, host, port):
        state["calls"].append(f"ping from {peer['body']['name']} {host}:{port}")
        return {"connected": state["there"], "answer": {"v": 1} if state["there"] else None}

    monkeypatch.setattr(core.pcs, "ping_from", ping_from)
    monkeypatch.setattr(firewall, "IS_WINDOWS", True)
    rules: set[str] = set()

    def add(name, protocol, port):
        rules.add(name)
        state["calls"].append(f"add {(name, protocol, port)}")

    def remove(name):
        rules.discard(name)
        state["calls"].append(f"remove {name}")

    monkeypatch.setattr(firewall, "has_rule", lambda name: name in rules)
    monkeypatch.setattr(firewall, "add", add)
    monkeypatch.setattr(firewall, "remove", remove)

    async def mcsrvstat(url, **kw):
        state["calls"].append(url)
        return {"online": state["internet"], "debug": {"cachehit": False}}

    monkeypatch.setattr(waysin.downloads, "fetch_json", mcsrvstat)
    return ctx, state


def lines_of(result, group):
    return [
        (line["what"], line["ok"], line["detail"])
        for line in result["lines"]
        if line["group"] == group
    ]


async def test_the_other_pc_gets_no_answer_so_the_firewall_rule_is_offered(core, ways):
    ctx, state = ways
    state["there"] = False
    first = await diagnose_round(core, ctx)
    port = ctx.core.ports.port_of(ctx).port
    assert lines_of(first, waysin.HOME) == [
        (f"From this PC: {HOME}:{port}", True, None),
        (f"From Laptop: {HOME}:{port}", False, waysin.NO_ANSWER),
    ]
    assert [(c["key"], c["safe"]) for c in first["changes"]] == [(f"firewall tcp {port}", False)]

    state["there"] = True  # the rule let it in
    second = await diagnose_round(core, ctx, first)
    name = firewall.rule_name("survival", "tcp", port)
    assert f"add ({name!r}, 'tcp', {port})" in state["calls"]
    assert second["done"][0]["outcome"] == "kept"
    assert second["changes"] == []


async def test_a_firewall_rule_that_didnt_help_is_taken_away(core, ways):
    ctx, state = ways
    state["there"] = False
    first = await diagnose_round(core, ctx)
    second = await diagnose_round(core, ctx, first)  # still no answer
    port = ctx.core.ports.port_of(ctx).port
    assert f"remove {firewall.rule_name('survival', 'tcp', port)}" in state["calls"]
    assert second["done"][0]["outcome"] == "put_back"


async def test_no_firewall_rule_when_ours_is_there_or_this_pc_gets_no_answer(
    core, ways, monkeypatch
):
    ctx, state = ways
    state["there"] = False
    monkeypatch.setattr(firewall, "has_rule", lambda name: True)
    assert (await diagnose_round(core, ctx))["changes"] == []
    monkeypatch.setattr(firewall, "has_rule", lambda name: False)
    state["here"] = False
    found = await diagnose_round(core, ctx)
    assert not any(c["key"].startswith("firewall") for c in found["changes"])


async def test_tailscale_switched_off_is_turned_back_on(core, ways):
    ctx, state = ways
    state["tailscale"] = "Stopped"
    first = await diagnose_round(core, ctx)
    assert lines_of(first, waysin.TAILSCALE) == [(waysin.TAILSCALE_ON, False, waysin.SWITCHED_OFF)]
    assert [(c["title"], c["safe"]) for c in first["changes"]] == [("Turn Tailscale back on", True)]

    state["tailscale"] = "Running"
    second = await diagnose_round(core, ctx, first, pick="safe")
    assert state["calls"][0] == "tailscale True"
    port = ctx.core.ports.port_of(ctx).port
    assert lines_of(second, waysin.TAILSCALE) == [(f"From this PC: {TS}:{port}", True, None)]
    assert second["done"][0]["outcome"] == "kept"


async def test_tailscale_signed_out_is_not_turned_on(core, ways):
    ctx, state = ways
    state["tailscale"] = "NeedsLogin"
    found = await diagnose_round(core, ctx)
    assert lines_of(found, waysin.TAILSCALE)[0][1] is False
    assert found["changes"] == []


async def test_no_typed_address_means_the_internet_is_not_checked(core, ways):
    ctx, state = ways
    found = await diagnose_round(core, ctx)
    [(what, ok, why)] = lines_of(found, waysin.INTERNET)
    assert ok is None and why.startswith("Not checked: no internet address is set.")
    assert not any("mcsrvstat" in call for call in state["calls"])


async def test_the_internet_gets_no_answer_so_the_router_step_is_listed(core, ways):
    ctx, state = ways
    layout.set_address(core.db, "survival", "play.example.com")
    state["internet"] = False
    found = await diagnose_round(core, ctx)
    assert "https://api.mcsrvstat.us/3/play.example.com" in state["calls"]
    port = ctx.core.ports.port_of(ctx).port
    assert lines_of(found, waysin.INTERNET) == [
        ("From the internet (mcsrvstat.us): play.example.com", False, waysin.NO_ANSWER)
    ]
    assert found["steps"] == [
        f"On your router, forward TCP port {port} to {HOME}. Command Block can't open "
        "router ports yet. Your router's page or manual says how."
    ]
    assert [c["key"] for c in found["changes"]] == [f"firewall tcp {port}"]


async def test_a_playit_address_is_listed_as_playit_with_no_router_or_firewall(core, ways):
    ctx, state = ways
    layout.set_address(core.db, "survival", "cool-name.joinmc.link")
    state["internet"] = False
    found = await diagnose_round(core, ctx)
    assert lines_of(found, waysin.PLAYIT)[0][1] is False
    assert found["steps"] == [] and found["changes"] == []


async def test_an_answer_mcsrvstat_kept_from_before_is_asked_again(core, ways, monkeypatch):
    ctx, state = ways
    layout.set_address(core.db, "survival", "play.example.com")
    monkeypatch.setattr(waysin, "STALE_WAIT", 0)
    answers = [
        {"online": False, "debug": {"cachehit": True, "cachetime": 1, "cacheexpire": 2}},
        {"online": True, "debug": {"cachehit": False, "cachetime": time.time() + 5}},
    ]

    async def mcsrvstat(url, **kw):
        return answers.pop(0)

    monkeypatch.setattr(waysin.downloads, "fetch_json", mcsrvstat)
    found = await diagnose_round(core, ctx)
    assert lines_of(found, waysin.INTERNET)[0][1] is True
    assert answers == []


def test_another_pc_may_only_ask_for_home_and_tailscale_addresses():
    assert waysin.may_ping("192.168.1.5") and waysin.may_ping("10.0.0.2")
    assert waysin.may_ping(TS)
    for host in ("127.0.0.1", "8.8.8.8", "example.com", "fd7a::1", ""):
        assert not waysin.may_ping(host)


def test_a_firewall_rule_name_is_only_ever_ours():
    assert firewall.rule_name("survival", "udp", 19132) == "CommandBlock-survival-UDP-19132"
    for bad in (("a b", "tcp", 1), ("x", "icmp", 1), ("x", "tcp", 0), ('x"', "tcp", 1)):
        with pytest.raises(firewall.FirewallError):
            firewall.rule_name(*bad)
