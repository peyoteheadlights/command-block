"""Join protection: the guard in front of a Java server.

Most tests open real TCP connections on this PC: test players speak the
handshake, a server list ping or Login Start to the guard, and the fake
game port behind it (tests/fixtures/fake_game_port.py) says what it saw.
On Linux all of 127.x.x.x is this PC, so players "from" 127.0.0.2,
127.0.0.3 ... count as different addresses; only 127.0.0.1 counts as this
PC itself. The rules (limits, blocks, burst, queue) are also checked on
their own with a fake clock. The agent tests start the fake server behind
the guard and check the settings, alerts and "Start when someone joins".
"""

from __future__ import annotations

import asyncio
import json
import socket
import struct
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import joinguard
from agent.joinguard import PRESETS, Listener, Rules, address_key, proxy_addon
from agent.main import create_app
from agent.security.auth import hash_password

from .conftest import PASSWORD, build_multi_config
from .fixtures.fake_game_port import FakeGamePort, mc_string, packet, varint

BASE = "/api/servers/survival"


# ------------------------------------------------------------ test players
def hello(intent: int, protocol: int = 767) -> bytes:
    body = varint(protocol) + mc_string("play.example") + struct.pack("!H", 25565)
    return packet(0, body + varint(intent))


def login_start(name: str) -> bytes:
    return packet(0, mc_string(name) + b"\x00" * 16)


async def read_packet(reader) -> tuple[int, bytes]:
    length, shift = 0, 0
    while True:
        byte = (await reader.readexactly(1))[0]
        length |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            break
    body = await reader.readexactly(length)
    return body[0], body[1:]


def text_of(body: bytes) -> str:
    length, pos, shift = 0, 0, 0
    while True:
        byte = body[pos]
        pos += 1
        length |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            break
    return body[pos : pos + length].decode()


async def connect(port: int, src: str = "127.0.0.2"):
    return await asyncio.open_connection("127.0.0.1", port, local_addr=(src, 0))


async def ping(port: int, src: str = "127.0.0.2", before: bytes = b"") -> dict | None:
    reader, writer = await connect(port, src)
    try:
        writer.write(before + hello(1) + packet(0, b""))
        await writer.drain()
        packet_id, body = await asyncio.wait_for(read_packet(reader), 5)
        status = json.loads(text_of(body))
        writer.write(packet(1, b"12345678"))
        await writer.drain()
        pong = await asyncio.wait_for(read_packet(reader), 5)
        assert pong == (1, b"12345678")
        return status
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    finally:
        writer.close()


async def join(port: int, name: str, src: str = "127.0.0.2", before: bytes = b"", keep=False):
    """("in", reader, writer) or ("out", message)."""
    reader, writer = await connect(port, src)
    writer.write(before + hello(2) + login_start(name))
    await writer.drain()
    try:
        packet_id, body = await asyncio.wait_for(read_packet(reader), 40)
    except (asyncio.IncompleteReadError, ConnectionError):
        writer.close()
        return ("out", None)
    if packet_id == 2:
        if keep:
            return ("in", reader, writer)
        writer.close()
        return ("in",)
    writer.close()
    return ("out", json.loads(text_of(body))["text"])


class Hub:
    """What a Listener asks the agent, for tests without one."""

    def __init__(self):
        self.woken: list[str] = []
        self.failed: list[str] = []

    def words(self, key, **values):
        return joinguard.WORDS[key].format(**values)

    def wake(self, server_id):
        self.woken.append(server_id)

    def failed_open(self, server_id, why):
        self.failed.append(why)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def game():
    server = FakeGamePort()
    yield server
    server.close()


def run(coro):
    return asyncio.run(coro)


async def guard_for(game, header=False, mode="proxy") -> Listener:
    listener = Listener(Hub(), "s", free_port(), "127.0.0.1")  # type: ignore[arg-type]
    listener.private, listener.header, listener.mode = game.port, header, mode
    listener.bind()
    await listener.serve()
    return listener


# ------------------------------------------------------------ pass-through
def test_pings_and_players_pass_through_to_the_server(game):
    async def go():
        guard = await guard_for(game)
        try:
            status = await ping(guard.port)
            assert status["description"]["text"] == "A fake server"
            got = await join(guard.port, "Steve", keep=True)
            assert got[0] == "in"
            _, reader, writer = got
            writer.write(b"hello there")
            await writer.drain()
            assert await reader.readexactly(11) == b"hello there"
            writer.close()
        finally:
            guard.close()

    run(go())
    assert game.seen[0]["name"] == "Steve"
    assert game.seen[0]["proxy"] is None  # no add-on: the server sees this PC


def test_the_real_address_goes_in_a_proxy_header_only_when_the_server_reads_it(game):
    async def go():
        guard = await guard_for(game, header=True)
        try:
            assert (await join(guard.port, "Alex", src="127.0.0.7"))[0] == "in"
        finally:
            guard.close()

    run(go())
    assert game.seen[0]["proxy"] == "127.0.0.7"


def test_bytes_that_arent_minecraft_are_closed_and_counted(game, monkeypatch):
    monkeypatch.setattr(joinguard, "HANDSHAKE_SECONDS", 0.3)

    async def go():
        guard = await guard_for(game)
        try:
            for _ in range(5):
                reader, writer = await connect(guard.port)
                writer.write(b"GET / HTTP/1.1\r\n\r\n")
                await writer.drain()
                assert await reader.read() == b""
                writer.close()
            return guard.rules.block_list()
        finally:
            guard.close()

    blocks = run(go())
    assert [b["address"] for b in blocks] == ["127.0.0.2"]
    assert game.seen == []


def test_a_slow_hello_is_cut_off(game, monkeypatch):
    monkeypatch.setattr(joinguard, "HANDSHAKE_SECONDS", 0.3)

    async def go():
        guard = await guard_for(game)
        try:
            reader, writer = await connect(guard.port)
            writer.write(b"\x10")  # starts a packet and stops
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), 3) == b""
            writer.close()
            return guard.rules.breaks.get("127.0.0.2")
        finally:
            guard.close()

    assert len(run(go())) == 1


# ------------------------------------------------------------ fail open
def test_a_check_that_throws_lets_the_player_through(game):
    async def go():
        guard = await guard_for(game)

        def broken(*_args):
            raise RuntimeError("a bug in a check")

        guard.rules.login = broken  # type: ignore[method-assign]
        try:
            return guard, await join(guard.port, "Steve")
        finally:
            guard.close()

    guard, got = run(go())
    assert got[0] == "in"
    assert guard.hub.failed == ["check"]


# ------------------------------------------------------------ limits
def test_more_connections_than_the_limit_from_one_address_are_closed(game):
    async def go():
        guard = await guard_for(game)
        try:
            held = [await connect(guard.port) for _ in range(3)]
            await asyncio.sleep(0.1)
            reader, writer = await connect(guard.port)
            assert await asyncio.wait_for(reader.read(), 3) == b""
            other = await ping(guard.port, src="127.0.0.3")  # another address still gets in
            for _, w in held:
                w.close()
            return other, guard.rules.breaks["127.0.0.2"]
        finally:
            guard.close()

    other, breaks = run(go())
    assert other is not None and len(breaks) == 1


def test_one_login_every_4_seconds_per_address(game):
    async def go():
        guard = await guard_for(game)
        try:
            first = await join(guard.port, "Steve")
            second = await join(guard.port, "Alex")
            third = await join(guard.port, "Sam", src="127.0.0.3")
            return first, second, third
        finally:
            guard.close()

    first, second, third = run(go())
    assert first[0] == "in" and third[0] == "in"
    assert second == ("out", joinguard.WORDS["join.too_fast"])


def test_too_many_pings_a_second_count_as_a_break(game):
    async def go():
        guard = await guard_for(game)
        guard.rules.limits["pings"] = 2
        try:
            results = [await ping(guard.port) for _ in range(3)]
            return results, guard.rules.breaks["127.0.0.2"]
        finally:
            guard.close()

    results, breaks = run(go())
    assert results[0] and results[1] and results[2] is None
    assert len(breaks) == 1


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def test_five_breaks_in_ten_minutes_block_for_an_hour_doubling_to_a_day():
    clock = Clock()
    rules = Rules(clock)
    for _ in range(4):
        assert not rules.broke("203.0.113.9", "pings")
    assert rules.broke("203.0.113.9", "pings")
    assert rules.refused("203.0.113.9", "203.0.113.9") == "blocked"
    assert rules.blocks["203.0.113.9"]["until"] == clock.now + 3600
    clock.now += 3601
    assert rules.refused("203.0.113.9", "203.0.113.9") is None
    lengths = []
    for _ in range(6):
        for _ in range(5):
            rules.broke("203.0.113.9", "pings")
        lengths.append(rules.blocks["203.0.113.9"]["until"] - clock.now)
        clock.now = rules.blocks["203.0.113.9"]["until"] + 1
    assert lengths == [7200, 14400, 28800, 57600, 86400, 86400]


def test_breaks_spread_over_more_than_ten_minutes_dont_block():
    clock = Clock()
    rules = Rules(clock)
    for _ in range(8):
        assert not rules.broke("203.0.113.9", "pings")
        clock.now += 200
    assert rules.block_list() == []


def test_owner_blocks_stay_until_removed():
    clock = Clock()
    rules = Rules(clock)
    rules.block("203.0.113.9", None, "owner", "admin")
    clock.now += 365 * 86400
    assert rules.refused("203.0.113.9", "203.0.113.9") == "blocked"
    assert rules.block_list()[0]["until"] is None
    assert rules.unblock("203.0.113.9")
    assert rules.refused("203.0.113.9", "203.0.113.9") is None


def test_ipv6_is_counted_per_64():
    assert address_key("2001:db8:1:2::5") == address_key("2001:db8:1:2:ffff::9")
    assert address_key("2001:db8:1:2::5") == "2001:db8:1:2::/64"
    assert address_key("2001:db8:1:3::5") != address_key("2001:db8:1:2::5")
    assert address_key("::ffff:203.0.113.9") == "203.0.113.9"


def test_the_two_sizes_are_the_agreed_numbers():
    assert PRESETS == {
        "small": {"connections": 3, "new_names": 15, "pings": 10, "slow_rate": 10},
        "big": {"connections": 6, "new_names": 60, "pings": 30, "slow_rate": 60},
    }
    assert (joinguard.LOGIN_EVERY, joinguard.HANDSHAKE_SECONDS) == (4.0, 5.0)


def test_home_network_and_tailscale_only():
    rules = Rules()
    rules.home_only = True
    for host in ("192.168.1.20", "10.0.0.5", "100.101.102.103", "fd7a:115c:a1e0::5", "127.0.0.1"):
        assert rules.refused(address_key(host), host) is None, host
    for host in ("203.0.113.9", "8.8.8.8", "2001:db8::1"):
        assert rules.refused(address_key(host), host) == "home_only", host


# ------------------------------------------------------------ the burst rule
def test_a_burst_of_new_names_lets_only_known_players_in_for_5_minutes():
    clock = Clock()
    rules = Rules(clock)
    rules.limits["new_names"] = 3
    rules.known = lambda name: name == "Steve"
    for i in range(2):
        assert rules.login(f"bot{i}", f"203.0.113.{i}", False) == ("in", None)
    assert rules.login("bot2", "203.0.113.2", False) == ("refuse", "join.known_only")
    assert rules.login("Steve", "198.51.100.1", False) == ("in", None)
    clock.now += 200
    for i in range(3, 6):  # still a rush: the 5 minutes start again
        rules.login(f"bot{i}", f"203.0.113.{i}", False)
    clock.now += 200
    assert rules.burst
    clock.now += 101
    assert not rules.burst
    assert rules.login("newfriend", "198.51.100.2", False) == ("in", None)
    assert rules.away_today() == 4


def test_pings_are_answered_from_the_last_good_answer_during_a_burst(game):
    async def go():
        guard = await guard_for(game)
        try:
            assert (await ping(guard.port))["players"]["online"] == 3
            guard.private = free_port()  # the server is swamped: nobody answers
            guard.rules.burst_until = time.time() + 300
            cached = await ping(guard.port, src="127.0.0.3")
            guard.rules.burst_until = 0
            guard.status_cache = None
            gone = await ping(guard.port, src="127.0.0.4")
            return cached, gone
        finally:
            guard.close()

    cached, gone = run(go())
    assert cached["description"]["text"] == "A fake server"
    assert gone is None  # outside a burst, never an answer the server didn't give


# ------------------------------------------------------------ Slow mode and the Join queue
def test_slow_mode_lets_new_names_in_at_a_rate_in_order():
    clock = Clock()
    rules = Rules(clock)
    rules.slow = True
    rules.limits.update(new_names=2, slow_rate=6)  # one every 10 seconds, at most 1 at once
    assert rules.login("a", "203.0.113.1", False) == ("in", None)  # no rush yet
    assert rules.login("b", "203.0.113.2", False) == ("in", None)  # the rush starts: 1 token
    assert rules.burst
    assert rules.login("c", "203.0.113.3", False) == ("wait", None)
    assert rules.login("d", "203.0.113.4", False) == ("wait", None)
    assert rules.place("c", "203.0.113.3") == 1 and rules.place("d", "203.0.113.4") == 2
    clock.now += 10
    assert not rules.turn("d", "203.0.113.4")  # c is first
    assert rules.turn("c", "203.0.113.3")
    clock.now += 10
    assert rules.turn("d", "203.0.113.4")


def test_a_place_in_line_is_kept_2_minutes_by_name_and_address():
    clock = Clock()
    rules = Rules(clock)
    rules.launch = True
    rules.limits.update(slow_rate=6)
    assert rules.turn("first", "203.0.113.1")  # takes the token
    assert not rules.turn("b", "203.0.113.2")
    rules.leave("b", "203.0.113.2")
    assert not rules.turn("c", "203.0.113.3")
    clock.now += 60
    assert rules.place("b", "203.0.113.2") == 1  # b came back within 2 minutes
    assert not rules.turn("b", "203.0.113.9")  # same name, other address: a new place
    assert rules.place("b", "203.0.113.9") == 3
    rules.leave("b", "203.0.113.2")
    clock.now += 121
    rules.turn("c", "203.0.113.3")
    assert rules._entry("b", "203.0.113.2") is None  # ran out


def test_the_queue_holds_a_player_then_lets_them_in(game, monkeypatch):
    monkeypatch.setattr(joinguard, "HOLD_SECONDS", 3.0)

    async def go():
        guard = await guard_for(game)
        guard.rules.launch = True
        guard.rules.limits.update(slow_rate=60)  # one a second
        try:
            first = await join(guard.port, "a", src="127.0.0.2")
            guard.rules.tokens = 0.0  # the next new name has to wait about a second
            started = time.time()
            second = await join(guard.port, "b", src="127.0.0.3")
            return first, second, time.time() - started
        finally:
            guard.close()

    first, second, waited = run(go())
    assert first[0] == "in" and second[0] == "in"
    assert 0.3 < waited < 3.0  # held on "Logging in..." and let in by itself


def test_a_long_wait_says_the_place_in_line(game, monkeypatch):
    monkeypatch.setattr(joinguard, "HOLD_SECONDS", 1.0)

    async def go():
        guard = await guard_for(game)
        guard.rules.launch = True
        guard.rules.limits.update(slow_rate=1)  # one a minute
        try:
            first = await join(guard.port, "a", src="127.0.0.2")
            second = await join(guard.port, "b", src="127.0.0.3")
            return first, second
        finally:
            guard.close()

    first, second = run(go())
    assert first[0] == "in"
    assert second == ("out", joinguard.WORDS["join.in_line"].format(place=1))


# ------------------------------------------------------------ playit
def proxy_v2(host: str, port: int) -> bytes:
    body = socket.inet_aton(host) + socket.inet_aton("127.0.0.1") + struct.pack("!HH", port, 25565)
    return joinguard.PROXY_V2 + bytes([0x21, 0x11]) + struct.pack("!H", len(body)) + body


def test_a_proxy_header_from_this_pc_gives_the_real_address(game):
    async def go():
        guard = await guard_for(game, header=True)
        try:
            got = await join(
                guard.port, "Steve", src="127.0.0.1", before=proxy_v2("203.0.113.5", 4)
            )
            return guard, got
        finally:
            guard.close()

    guard, got = run(go())
    assert got[0] == "in"
    assert game.seen[0]["proxy"] == "203.0.113.5"
    assert guard.playit_seen and guard.log[-1]["address"] == "203.0.113.5"


def test_a_proxy_header_from_anywhere_else_isnt_believed(game):
    async def go():
        guard = await guard_for(game, header=True)
        try:
            got = await join(
                guard.port, "Steve", src="127.0.0.2", before=proxy_v2("203.0.113.5", 4)
            )
            return guard, got
        finally:
            guard.close()

    guard, got = run(go())
    assert got[0] == "out" and not guard.playit_seen and game.seen == []


def test_this_pc_without_a_header_skips_per_address_limits(game):
    async def go():
        guard = await guard_for(game)
        try:
            return [await join(guard.port, f"p{i}", src="127.0.0.1") for i in range(4)]
        finally:
            guard.close()

    assert [got[0] for got in run(go())] == ["in"] * 4


# ------------------------------------------------------------ asleep
def test_a_slept_server_says_so_and_a_login_wakes_it(game):
    async def go():
        guard = await guard_for(game, mode="asleep")
        guard.asleep_words = joinguard.WORDS["join.asleep"]
        try:
            status = await ping(guard.port)
            got = await join(guard.port, "Steve")
            return guard, status, got
        finally:
            guard.close()

    guard, status, got = run(go())
    assert status["description"]["text"] == "Asleep, join to start it"
    assert status["players"]["online"] == 0 and status["version"]["protocol"] == 767
    assert got == ("out", joinguard.WORDS["join.starting"])
    assert guard.hub.woken == ["s"] and game.seen == []


# ------------------------------------------------------------ the add-on on disk
def make_ctx(tmp_path: Path, type_id: str):
    from agent import servertypes

    class Config:
        server_type = servertypes.get(type_id)
        server_dir = tmp_path

    class Ctx:
        config = Config()

    return Ctx()


def test_the_header_needs_the_types_add_on_and_its_setting_on_disk(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "mods").mkdir()
    assert proxy_addon(make_ctx(tmp_path, "paper")) is None
    (tmp_path / "config" / "paper-global.yml").write_text("proxies:\n  proxy-protocol: true\n")
    assert proxy_addon(make_ctx(tmp_path, "paper")) == "Paper"
    assert proxy_addon(make_ctx(tmp_path, "purpur")) == "Paper"

    assert proxy_addon(make_ctx(tmp_path, "fabric")) is None
    jar = tmp_path / "mods" / "proxy-protocol-support-1.1.0.jar"
    jar.write_bytes(b"jar")
    assert proxy_addon(make_ctx(tmp_path, "fabric")) == "Proxy Protocol Support"  # on by default
    (tmp_path / "config" / "proxy_protocol_support.json").write_text(
        '{"enableProxyProtocol": false}'
    )
    assert proxy_addon(make_ctx(tmp_path, "fabric")) is None
    jar.unlink()

    (tmp_path / "mods" / "HAProxyCompat-1.1.0.jar").write_bytes(b"jar")
    assert proxy_addon(make_ctx(tmp_path, "fabric")) == "HAProxyCompat"
    assert proxy_addon(make_ctx(tmp_path, "neoforge")) == "HAProxyCompat"
    (tmp_path / "config" / "haproxycompat-common.toml").write_text("enabled = false\n")
    assert proxy_addon(make_ctx(tmp_path, "neoforge")) is None
    assert proxy_addon(make_ctx(tmp_path, "forge")) is None
    (tmp_path / "mods" / "properproxyprotocol-1.1.0.jar").write_bytes(b"jar")
    assert proxy_addon(make_ctx(tmp_path, "forge")) == "ProperProxyProtocol"
    assert proxy_addon(make_ctx(tmp_path, "vanilla")) is None


def test_proxy_add_ons_are_offered_for_their_types():
    rows = json.loads((Path(__file__).parents[1] / "data" / "catalog.json").read_text())
    proxies = {
        r["name"]: r["types"] for r in rows["recommended_addons"] if r.get("role") == "proxy"
    }
    assert proxies == {
        "Proxy Protocol Support": ["fabric", "quilt"],
        "HAProxyCompat": ["neoforge"],
        "ProperProxyProtocol": ["forge"],
    }


# ------------------------------------------------------------ the agent
@pytest.fixture
def agent(tmp_path, monkeypatch):
    """The real agent with two fake servers. survival is Fabric (the fake
    server listens when started with --port)."""
    monkeypatch.setattr(joinguard, "RAW_COMMAND_OK", True)
    monkeypatch.setattr(joinguard, "SUPERVISE_SECONDS", 0.2)
    config = build_multi_config(
        tmp_path,
        servers=[("survival", "Survival", free_port()), ("creative", "Creative", free_port())],
    )
    monkeypatch.setenv("MCSC_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MCSC_ADMIN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))
    monkeypatch.delenv("MCSC_API_TOKEN", raising=False)
    with TestClient(create_app(config)) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        client.headers["Authorization"] = f"Bearer {token.json()['token']}"
        yield client


def ctx_of(client, server_id="survival"):
    return client.app.state.core.servers[server_id]


def wait_for(check, seconds=15.0):
    end = time.time() + seconds
    while time.time() < end:
        found = check()
        if found:
            return found
        time.sleep(0.1)
    raise AssertionError("waited too long")


def settings(client, **values):
    response = client.put(f"{BASE}/settings", json={"updates": values})
    assert response.status_code == 200, response.text
    assert not response.json()["rejected"], response.json()
    return response.json()


def start(client):
    assert client.post(f"{BASE}/server/start").status_code == 200, "start"
    wait_for(lambda: client.get(f"{BASE}/status").json()["state"] == "ONLINE")


def stop(client):
    assert client.post(f"{BASE}/server/stop", json={}).status_code == 200
    wait_for(lambda: client.get(f"{BASE}/status").json()["state"] == "OFFLINE")


def props(client) -> str:
    return (Path(ctx_of(client).config.server_dir) / "server.properties").read_text()


def alerts(client, kind):
    rows = client.app.state.core.db.recent_events("survival", limit=200)
    return [row for row in rows if row["type"] == kind]


def test_turning_it_on_puts_the_server_behind_the_guard(agent):
    settings(agent, **{"server.join_protection": True})
    (Path(ctx_of(agent).config.server_dir) / "server.properties").write_text(
        f"server-port={ctx_of(agent).config.server.port}\nprevent-proxy-connections=true\n"
    )
    start(agent)
    ctx = ctx_of(agent)
    public = ctx.core.ports.port_of(ctx).port
    assert "server-ip=127.0.0.1" in props(agent)
    assert "prevent-proxy-connections=false" in props(agent)  # Vanilla fallback
    assert "--port" in ctx.server.launch_args
    assert ctx.server.detected_port == public
    status = asyncio.run(ping(public))
    assert status["description"]["text"] == "A fake server"
    assert asyncio.run(join(public, "Steve"))[0] == "in"
    data = agent.get(f"{BASE}/join-protection").json()
    assert data["status"]["running"] and data["status"]["real_addresses"] is False
    stop(agent)
    settings(agent, **{"server.join_protection": False})
    start(agent)
    assert "server-ip=127.0.0.1" not in props(agent)
    assert "prevent-proxy-connections=true" in props(agent)  # put back as it was
    assert ctx.server.launch_args == []
    stop(agent)


def test_a_port_the_guard_cant_take_means_the_server_starts_direct(agent):
    settings(agent, **{"server.join_protection": True})
    ctx = ctx_of(agent)
    with socket.socket() as holder:
        holder.bind(("0.0.0.0", ctx.core.ports.port_of(ctx).port))
        holder.listen(1)
        agent.post(f"{BASE}/server/start")
        wait_for(lambda: alerts(agent, "join_protection_off"))
    assert ctx.server.launch_args == []
    assert "server-ip=127.0.0.1" not in props(agent)
    assert alerts(agent, "join_protection_off")[0]["level"] == "warn"


def test_a_stopped_guard_is_started_again_with_an_alert(agent):
    settings(agent, **{"server.join_protection": True})
    start(agent)
    hub = ctx_of(agent).core.guard
    public = ctx_of(agent).core.ports.port_of(ctx_of(agent)).port
    hub.loop.call_soon_threadsafe(hub.loop.stop)
    wait_for(lambda: alerts(agent, "join_protection_off"))
    assert json.loads(alerts(agent, "join_protection_off")[0]["data"])["why"] == "restarted"
    assert wait_for(lambda: asyncio.run(ping(public)))["players"]["online"] == 3
    stop(agent)


def test_start_when_someone_joins_wakes_a_slept_server_once(agent):
    settings(agent, **{"server.start_on_join": True})
    ctx = ctx_of(agent)
    public = ctx.core.ports.port_of(ctx).port
    ctx.server.stopped_for_sleep = True  # as auto-sleep leaves it
    wait_for(lambda: ctx.core.guard.status(ctx)["asleep"])
    status = asyncio.run(ping(public))
    assert status["description"]["text"] == "Asleep, join to start it"
    first = asyncio.run(join(public, "Steve"))
    assert first == ("out", joinguard.WORDS["join.starting"])
    try:  # a second friend while it starts: the message, or the port is already the server's
        asyncio.run(join(public, "Alex", src="127.0.0.3"))
    except OSError:
        pass
    wait_for(lambda: agent.get(f"{BASE}/status").json()["state"] == "ONLINE")
    starts = [r for r in ctx.db.audit_entries() if r.get("action") == "start_on_join"]
    assert len(starts) == 1
    stop(agent)  # a manual stop: not woken again
    time.sleep(0.6)
    assert not ctx.core.guard.status(ctx)["asleep"]
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", public), timeout=1)


def test_launch_mode_needs_a_set_up_server_and_ends_by_itself(agent):
    (Path(ctx_of(agent).config.server_dir) / "world" / "level.dat").unlink()
    response = agent.post(f"{BASE}/join-protection/launch", json={"hours": 24, "players": 100})
    assert response.status_code == 400  # no world yet
    ctx = ctx_of(agent)
    folder = Path(ctx.config.server_dir)
    (folder / "world").mkdir(exist_ok=True)
    (folder / "world" / "level.dat").write_bytes(b"x")
    response = agent.post(f"{BASE}/join-protection/launch", json={"hours": 24, "players": 100})
    assert response.status_code == 200, response.text
    status = agent.get(f"{BASE}/join-protection").json()["status"]
    assert status["launch_until"] > time.time() + 23 * 3600 and status["slow"]
    assert status["limits"]["slow_rate"] == 50 and status["limits"]["connections"] == 6
    for bad in ({"hours": 0.5, "players": 10}, {"hours": 169, "players": 10}):
        assert agent.post(f"{BASE}/join-protection/launch", json=bad).status_code == 422
    ctx.config.set("server.launch_until", time.time() - 1)
    wait_for(lambda: alerts(agent, "launch_mode_ended"))
    assert agent.get(f"{BASE}/join-protection").json()["status"]["launch_until"] is None


def test_blocks_can_be_added_and_removed_and_are_kept(agent):
    response = agent.post(f"{BASE}/join-protection/blocks", json={"address": "2001:db8:1:2::9"})
    assert response.status_code == 200, response.text
    blocks = agent.get(f"{BASE}/join-protection").json()["blocks"]
    assert blocks[0]["address"] == "2001:db8:1:2::/64" and blocks[0]["until"] is None
    assert ctx_of(agent).db.get_setting("join_blocks")["2001:db8:1:2::/64"]["by"] == "admin"
    assert (
        agent.post(f"{BASE}/join-protection/blocks", json={"address": "nonsense"}).status_code
        == 400
    )
    response = agent.request(
        "DELETE", f"{BASE}/join-protection/blocks", json={"address": "2001:db8:1:2::9"}
    )
    assert response.status_code == 200
    assert agent.get(f"{BASE}/join-protection").json()["blocks"] == []


def test_size_follows_who_its_for_and_the_owners_pick(agent):
    ctx = ctx_of(agent)
    assert joinguard.size(ctx) == "small"
    ctx.config.set("server.played_by", "community")
    assert joinguard.size(ctx) == "big"
    ctx.config.set("server.played_by", "friends")
    ctx.db.set_setting("join_peak", {"count": 20, "at": time.time()})
    assert joinguard.size(ctx) == "big"
    ctx.db.set_setting("join_peak", {"count": 20, "at": time.time() - 31 * 86400})
    assert joinguard.size(ctx) == "small"
    settings(agent, **{"server.join_size": "big", "server.join_limits": {"pings": 50}})
    assert joinguard.limits(ctx)["pings"] == 50 and joinguard.limits(ctx)["connections"] == 6
    response = agent.put(f"{BASE}/settings", json={"updates": {"server.join_size": "huge"}})
    assert response.json()["rejected"]
    response = agent.put(
        f"{BASE}/settings", json={"updates": {"server.join_limits": {"pings": -1}}}
    )
    assert response.json()["rejected"]


def test_the_card_suggests_it_and_says_when_its_on(agent):
    ctx = ctx_of(agent)
    ctx.config.set("server.played_by", "community")
    lines = agent.get(f"{BASE}/protection").json()["lines"]
    line = next(x for x in lines if x["id"] == "join_off")
    assert line["fix"] == "join_on"
    assert agent.post(f"{BASE}/protection/fix", json={"fix": "join_on"}).status_code == 200
    assert ctx.config.server.join_protection
    lines = agent.get(f"{BASE}/protection").json()["lines"]
    assert next(x for x in lines if x["id"] == "join_ready")["level"] == "info"
    start(agent)
    lines = agent.get(f"{BASE}/protection").json()["lines"]
    on = next(x for x in lines if x["id"] == "join_on")
    assert on["values"]["blocked"] == 0
    assert any(x["id"] == "join_vanilla_fallback" for x in lines)
    stop(agent)


def test_ban_ip_goes_to_the_guard_when_the_server_sees_only_this_pc(agent):
    settings(agent, **{"server.join_protection": True})
    start(agent)
    public = ctx_of(agent).core.ports.port_of(ctx_of(agent)).port
    asyncio.run(join(public, "Griefer", src="127.0.0.9"))
    response = agent.post(
        f"{BASE}/server/command", json={"command": "ban-ip Griefer", "confirm": True}
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"] == "BLOCKED"
    blocks = agent.get(f"{BASE}/join-protection").json()["blocks"]
    assert blocks[0]["address"] == "127.0.0.9"
    assert asyncio.run(join(public, "Griefer", src="127.0.0.9")) == ("out", None)
    response = agent.post(
        f"{BASE}/server/command", json={"command": "pardon-ip 127.0.0.9", "confirm": True}
    )
    assert response.json()["result"] == "BLOCKED"
    assert agent.get(f"{BASE}/join-protection").json()["blocks"] == []
    log = agent.get(f"{BASE}/join-protection").json()["log"]
    assert log[0]["name"] == "Griefer" and log[0]["address"] == "127.0.x.x"  # kept masked
    stop(agent)


def test_bedrock_stays_outside_the_guard():
    from agent import servertypes

    assert not servertypes.get("bedrock").wakes_on_join
    assert all(t.wakes_on_join for t in servertypes.TYPES.values() if t.edition == "java")
