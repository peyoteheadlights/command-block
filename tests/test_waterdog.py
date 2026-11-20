"""Plain Bedrock through playit.gg: WaterdogPE in front of the server, the
Companion's plugin keeping its allow list (agent/waterdog.py)."""

from __future__ import annotations

import asyncio
import json
import sys

import pytest
from fastapi.testclient import TestClient

from agent import downloads, internet, playit, waterdog
from agent.minecraft import properties
from agent.security.auth import hash_password

from .conftest import build_bedrock_config
from .test_playit import FakeProgram, running, tunnel

PASSWORD = "correct horse battery staple"
BASE = "/api/servers/bedrock"
TOKEN = "playit-tunnel-token-123"
BOX = f"""
listener:
  host: 0.0.0.0:19132
  transport: nethernet
  priorities: [bedrock]
servers:
  bedrock:
    address: 127.0.0.1:19142
    server_type: bedrock
nethernet:
  signaling:
    mode: nxs
    nxs:
      token: {TOKEN}
online_mode: true
"""


@pytest.fixture
def downloaded(monkeypatch):
    """WaterdogPE and the plugin "downloaded": files written, nothing fetched."""
    asked = []

    async def download(folder, repo, tag, filename, name):
        asked.append((repo, tag, filename))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"PK fake jar")
        return folder / name

    monkeypatch.setattr(waterdog, "download", download)
    monkeypatch.setattr(waterdog, "check_java", lambda java: None)
    return asked


@pytest.fixture
def client(tmp_path, monkeypatch, downloaded):
    monkeypatch.setenv("MCSC_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MCSC_ADMIN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))
    monkeypatch.delenv("MCSC_API_TOKEN", raising=False)
    fake = FakeProgram()
    fake.life = running()
    monkeypatch.setattr(playit, "ask", fake.ask)
    internet._states.clear()
    with TestClient(create_app(build_bedrock_config(tmp_path))) as c:
        token = c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        c.headers["Authorization"] = f"Bearer {token.json()['token']}"
        c.fake = fake  # type: ignore[attr-defined]
        yield c


def create_app(config):
    from agent.main import create_app as make

    return make(config)


def ctx(c):
    return c.app.state.core.servers["bedrock"]


def props(c) -> dict:
    return properties.PropertiesFile.read(ctx(c).config.server_dir / "server.properties").values()


# ---------------------------------------------------------------- playit's box
def test_the_box_gives_the_ports_and_keeps_the_token_in_the_config():
    found = waterdog.read_box(BOX)
    assert (found["listen"], found["media"], found["server_port"]) == (19132, 19132, 19142)
    assert found["config"]["nethernet"]["signaling"]["nxs"]["token"] == TOKEN


@pytest.mark.parametrize(
    "box, words",
    [
        ("", "Paste"),
        ("listener: [", "can't be read"),
        ("listener: {host: '0.0.0.0:19132'}", "no nethernet"),
        (BOX.replace("127.0.0.1:19142", "10.0.0.5:19142"), "not to this PC"),
        (BOX.replace("127.0.0.1:19142", "127.0.0.1:19132"), "same port"),
        (BOX.replace("0.0.0.0:19132", "0.0.0.0"), "need a port"),
    ],
)
def test_anything_but_playits_box_is_refused_in_words(box, words):
    with pytest.raises(waterdog.WaterdogError, match=words):
        waterdog.read_box(box)


def test_netherNets_port_is_worked_out_like_waterdogpe_does():
    both = {"listener": {"transport": "both"}, "servers": {"a": {"address": "127.0.0.1:19133"}}}
    assert waterdog.media_port(both, 19132) == 19134  # the next one no server uses
    assert waterdog.media_port({"listener": {"transport": "nethernet"}}, 19132) == 19132
    assert waterdog.media_port({"nethernet": {"webrtc_port": 19200}}, 19132) == 19200


# ---------------------------------------------------------------- setting it up
def test_set_up_moves_the_server_and_writes_both_configs(client, downloaded, tmp_path):
    folder = ctx(client).config.server_dir
    path = folder / "server.properties"
    path.write_text(
        path.read_text(encoding="utf-8").replace("allow-list=false", "allow-list=true")
        + "server-udp-ports=19132\nonline-mode=true\n",
        encoding="utf-8",
    )
    answer = client.post(f"{BASE}/playit-bedrock", json={"box": BOX})
    assert answer.status_code == 200, answer.text
    assert TOKEN not in answer.text  # never sent back
    now = props(client)
    assert now["server-port"] == "19142" and now["server-portv6"] == "19143"
    assert now["server-udp-ports"] == "19142"  # follows the port (11c part 0b)
    assert now["online-mode"] == "false" and now["allow-list"] == "false"
    assert now["enable-lan-visibility"] == "false"
    assert [d[2] for d in downloaded] == [waterdog.JAR, waterdog.PLUGIN_JAR]
    assert downloaded[1][:2] == (downloads.REPO, waterdog.COMPANION_TAG)
    home = client.app.state.core.waterdog.folder(ctx(client))
    written = (home / "config.yml").read_text(encoding="utf-8")
    assert TOKEN in written
    assert "enable_statistics: false" in written and "enable_error_reporting: false" in written
    status = answer.json()["waterdog"]
    assert status["list_on"] is True and status["server_port"] == 19142
    assert answer.json()["ways"] == [{"kind": "nethernet", "local_port": 19132, "tunnel": None}]


def test_the_router_switch_goes_off_and_stays_off(client):
    ctx(client).config.set("server.internet", True)
    answer = client.post(f"{BASE}/playit-bedrock", json={"box": BOX}).json()
    assert answer["internet_off"] is True
    assert ctx(client).config.server.internet is False
    again = client.put(f"{BASE}/internet", json={"on": True})
    assert again.status_code == 409 and "WaterdogPE" in again.json()["detail"]


def test_a_running_server_or_a_taken_port_is_refused(client, downloaded):
    core = client.app.state.core
    core.ports.used = lambda exclude=None: {("udp", 19132): "bedrock"}  # pretend another server
    refused = client.post(f"{BASE}/playit-bedrock", json={"box": BOX})
    assert refused.status_code == 409 and "already uses port 19132" in refused.json()["detail"]
    assert downloaded == []  # nothing downloaded before the checks pass


def test_only_the_owner_sets_it_up(client, monkeypatch, downloaded):
    from agent.security.auth import Principal

    monkeypatch.setattr(Principal, "is_owner", property(lambda self: False))
    assert client.post(f"{BASE}/playit-bedrock", json={"box": BOX}).status_code == 403
    assert client.delete(f"{BASE}/playit-bedrock").status_code == 403
    assert downloaded == []


def test_the_nethernet_tunnel_is_found_and_fills_the_address(client):
    client.post(f"{BASE}/playit-bedrock", json={"box": BOX})
    client.fake.tunnels = [tunnel("cool.tun.ply.gg", 19132)]
    client.fake.life = running(*client.fake.tunnels)
    playit_state = client.app.state.core.playit
    playit_state._save(on=True)
    now = client.get(f"{BASE}/playit").json()
    assert now["ways"][0]["tunnel"]["address"] == "cool.tun.ply.gg"
    assert playit_state.tunnel_address(ctx(client)) == "cool.tun.ply.gg"
    assert now["proxy"] == "none"  # playit's guide: NetherNet tunnels have none
    # mcsrvstat.us can't test NetherNet: its "no" would be a guess.
    test = client.post(f"{BASE}/playit-test", json={"allow_mcsrvstat": True})
    assert test.status_code == 409


def test_waterdogs_ports_are_claimed_so_no_other_server_takes_them(client):
    client.post(f"{BASE}/playit-bedrock", json={"box": BOX})
    claimed = {(p.protocol, p.port) for p in client.app.state.core.ports.claimed(ctx(client))}
    assert {("udp", 19132), ("udp", 19142), ("tcp", 19142)} <= claimed


# ---------------------------------------------------------------- with the server
def test_an_allow_list_turned_on_later_becomes_the_plugins(client):
    client.post(f"{BASE}/playit-bedrock", json={"box": BOX})
    hub = client.app.state.core.waterdog
    path = ctx(client).config.server_dir / "server.properties"
    hub._put(ctx(client), {**hub.of(ctx(client)), "list_on": False})
    path.write_text(path.read_text(encoding="utf-8").replace("allow-list=false", "allow-list=true"))
    said = hub.before_launch(ctx(client))
    assert "allow-list=false" in said
    assert props(client)["allow-list"] == "false"
    assert hub.of(ctx(client))["list_on"] is True
    assert hub.before_launch(ctx(client)) is None  # nothing to change twice


FAKE_WATERDOG = """
import sys
print("Starting WaterdogPE", flush=True)
print("Registered with the external signaling service. Players can join", flush=True)
for line in sys.stdin:
    if line.strip() == "end":
        print("bye", flush=True)
        break
"""


def test_waterdog_runs_with_the_server_and_the_plugin_gets_the_list(client, monkeypatch):
    client.post(f"{BASE}/playit-bedrock", json={"box": BOX})
    hub = client.app.state.core.waterdog
    started = []

    async def start(config, cmd, *, cwd, **popen):
        started.append((cmd, cwd))
        return await asyncio.create_subprocess_exec(
            sys.executable, "-c", FAKE_WATERDOG, cwd=str(cwd), **popen
        )

    monkeypatch.setattr(waterdog.serveruser, "start", start)

    async def run():
        await hub.start(ctx(client))
        for _ in range(100):
            if hub.state.get("bedrock") == "registered":
                break
            await asyncio.sleep(0.05)
        state = hub.state.get("bedrock")
        await hub.stop(ctx(client))
        return state

    assert asyncio.run(run()) == "registered"
    cmd, cwd = started[0]
    assert cmd[-2:] == ["-jar", waterdog.JAR] and cwd == hub.folder(ctx(client))
    plugin = json.loads((cwd / waterdog.PLUGIN_CONFIG).read_text(encoding="utf-8"))
    assert plugin == {"format": 1, "allow_list": None}  # allow list was off
    assert "bedrock" not in hub.processes and hub.state["bedrock"] == "stopped"


def test_stop_using_puts_the_server_back_and_deletes_the_token(client):
    before = props(client)
    client.post(f"{BASE}/playit-bedrock", json={"box": BOX})
    home = client.app.state.core.waterdog.folder(ctx(client))
    assert home.is_dir()
    answer = client.delete(f"{BASE}/playit-bedrock")
    assert answer.status_code == 200, answer.text
    assert answer.json()["waterdog"] is None
    after = props(client)
    assert after["server-port"] == before["server-port"]
    assert after["server-portv6"] == before["server-portv6"]
    # Keys the file didn't have get Bedrock's defaults: the sign-in check is back.
    assert after["online-mode"] == "true" and after["enable-lan-visibility"] == "true"
    assert after["allow-list"] == "false"
    assert not home.exists()


# ---------------------------------------------------------------- the download
def test_nothing_unchecked_is_downloaded(tmp_path, monkeypatch):
    from agent.mods import github

    async def release(self, repo, tag=None):
        return {"files": [{"file": {"filename": waterdog.JAR, "url": "https://x", "sha256": None}}]}

    monkeypatch.setattr(github.GitHubReleases, "release", release)

    async def try_it():
        await waterdog.download(tmp_path, waterdog.REPO, waterdog.TAG, waterdog.JAR, waterdog.JAR)

    with pytest.raises(waterdog.WaterdogError, match="no checked"):
        asyncio.run(try_it())
    assert not (tmp_path / waterdog.JAR).exists()
