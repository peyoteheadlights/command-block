"""Bedrock 1.26.50+ (NetherNet) ports: the game port is TCP and UDP, the
UDP game traffic goes to server-udp-ports, and Diagnose server opens both.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import diagnose, diagnosefix, firewall, gamesettings, joinbot, ports
from agent.core import AgentCore
from agent.minecraft import nethernet

from .conftest import build_bedrock_config, make_bedrock_folder


def props(folder):
    return (folder / "server.properties").read_text(encoding="utf-8")


def test_udp_ports_are_set_to_the_game_port_once(tmp_path):
    folder = make_bedrock_folder(tmp_path / "b", port=19140)
    assert ports.set_udp_ports(folder) == 19140
    assert "server-udp-ports=19140" in props(folder)
    assert "# Mojang's comments stay" in props(folder)
    assert ports.set_udp_ports(folder) is None  # already set: left alone


def test_the_owners_own_udp_ports_are_never_changed(tmp_path):
    folder = make_bedrock_folder(tmp_path / "b")
    with open(folder / "server.properties", "a", encoding="utf-8") as f:
        f.write("server-udp-ports=19200-19210\n")
    assert ports.set_udp_ports(folder) is None
    assert ports.udp_ports(folder) == "19200-19210"


@pytest.mark.parametrize(
    "value, shown",
    [("19132", "19132"), ("19140-19155", "19140-19155"), ("1.2.3.4:19132:19132", None), ("", None)],
)
def test_the_router_step_shows_only_a_plain_port_or_range(tmp_path, value, shown):
    folder = make_bedrock_folder(tmp_path / "b")
    with open(folder / "server.properties", "a", encoding="utf-8") as f:
        f.write(f"server-udp-ports={value}\n")
    assert ports.udp_ports(folder) == shown


def fake_ctx(edition):
    return SimpleNamespace(
        server_id="s1", config=SimpleNamespace(server_type=SimpleNamespace(edition=edition))
    )


def no_answer_outside(protocol, port):
    return [
        {
            "protocol": protocol,
            "port": port,
            "outside": False,
            "ok": True,
            "group": "Home network",
            "detail": None,
        },
        {
            "protocol": protocol,
            "port": port,
            "outside": True,
            "ok": False,
            "group": "Home network",
            "detail": diagnosefix.waysin.NO_ANSWER,
        },
    ]


def test_a_bedrock_server_gets_a_tcp_and_a_udp_firewall_rule(monkeypatch):
    monkeypatch.setattr(firewall, "IS_WINDOWS", True)
    monkeypatch.setattr(firewall, "has_rule", lambda name: False)
    found = diagnosefix._firewall(fake_ctx("bedrock"), no_answer_outside("raknet", 19132), [])
    assert sorted(found) == ["firewall tcp 19132", "firewall udp 19132"]


def test_geyser_still_gets_only_a_udp_rule(monkeypatch):
    monkeypatch.setattr(firewall, "IS_WINDOWS", True)
    monkeypatch.setattr(firewall, "has_rule", lambda name: False)
    found = diagnosefix._firewall(fake_ctx("java"), no_answer_outside("raknet", 19132), [])
    assert sorted(found) == ["firewall udp 19132"]


async def test_the_join_bot_isnt_sent_to_a_nethernet_server(tmp_path, monkeypatch):
    folder = make_bedrock_folder(tmp_path / "b")
    ctx = SimpleNamespace(
        config=SimpleNamespace(server_dir=folder, server_type=SimpleNamespace(edition="bedrock"))
    )
    lines = []
    run = SimpleNamespace(line=lambda *a: lines.append(a))
    monkeypatch.setattr(nethernet, "ping", lambda host, port: {"version": "1.26.60"})

    async def no_bot(*a, **kw):
        raise AssertionError("the test player was sent")

    monkeypatch.setattr(joinbot, "join", no_bot)
    join = diagnose.Join("Join from this PC", "bedrock", 19132, False)
    await diagnose._join(ctx, run, None, join)
    assert lines == [("Join from this PC", "Join", None, diagnose.NETHERNET_NOT_CHECKED)]


async def test_two_bedrock_servers_on_one_port_get_one_reason(tmp_path):
    config = build_bedrock_config(tmp_path)
    core = AgentCore(config)
    try:
        first = core.servers["bedrock"]
        second = SimpleNamespace(
            config=SimpleNamespace(
                server_type=first.config.server_type,
                server_dir=make_bedrock_folder(tmp_path / "other"),
                server=SimpleNamespace(crossplay=False),
            ),
            server=SimpleNamespace(running=True, detected_port=None),
            name="Other",
        )
        core.servers["other"] = second
        problems = core.ports.start_conflicts(first)
        assert [p for p in problems if p.startswith("Port 19132")] == [problems[0]]
        assert sum("19132" in p for p in problems) == 1
    finally:
        await core.jobs.stop()
        core.db.close()


async def test_changing_the_port_moves_udp_ports_set_to_it(tmp_path):
    config = build_bedrock_config(tmp_path)
    core = AgentCore(config)
    try:
        ctx = core.servers["bedrock"]
        ports.set_udp_ports(ctx.config.server_dir)
        await gamesettings.save(ctx, updates={"server-port": 19150})
        text = props(ctx.config.server_dir)
        assert "server-port=19150" in text and "server-udp-ports=19150" in text
    finally:
        await core.jobs.stop()
        core.db.close()
