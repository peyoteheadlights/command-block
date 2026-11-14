# ruff: noqa: F811  (the fixtures imported from other tests are used by name)
"""The download link and the server resource pack.

The link is a real listener here (on 127.0.0.1, standing in for the
Tailscale address), fetched the way a player's game or launcher would.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import urllib.error
import urllib.request

import pytest

from agent import downloadlink, modpackmake
from agent.minecraft.properties import PropertiesFile

from .test_curseforge import fabric, relay  # noqa: F401
from .test_hangar_and_links import zip_of
from .test_modpack_make import PACK, FakeBot, api, make, server  # noqa: F401
from .test_own_jars import moderator


@pytest.fixture
def linked(server, monkeypatch):
    """The server, with the agent moved onto "Tailscale" (127.0.0.1) and
    server.properties there."""
    client, headers = server
    monkeypatch.setattr(downloadlink.LinkServer, "address", lambda self: "127.0.0.1")
    props = client.app.state.core.mods.config.server_dir / "server.properties"
    if not props.is_file():
        props.write_text("motd=hi\n")
    return client, headers


def fetch(url: str, method: str = "GET"):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method), timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), b""


def switch(client, headers, on: bool):
    return client.post(api(client, "download-link"), json={"on": on}, headers=headers)


def props(client) -> dict[str, str]:
    path = client.app.state.core.mods.config.server_dir / "server.properties"
    return PropertiesFile.read(path).values()


# ---------------------------------------------------------------- the link
def test_the_link_is_off_by_default_and_needs_tailscale(server):
    client, headers = server
    state = client.get(api(client, "download-link"), headers=headers).json()
    assert state["on"] is False and state["modpack_url"] is None
    assert "Tailscale" in state["why_not"]
    refused = switch(client, headers, True)
    assert refused.status_code == 400 and "Tailscale" in refused.json()["detail"]


def test_friends_download_the_newest_modpack_through_the_link(linked):
    client, headers = linked
    answer = switch(client, headers, True)
    assert answer.status_code == 200, answer.text
    state = answer.json()
    assert state["on"] and state["listening"] and state["modpack_url"] is None  # none made yet
    made = make(client, headers).json()
    link = client.get(api(client, "download-link"), headers=headers).json()["modpack_url"]
    assert link.startswith("http://127.0.0.1:") and link.endswith("/modpack.mrpack")
    status, got, body = fetch(link)
    assert status == 200 and body.startswith(b"PK")
    assert got["Content-Type"] == "application/zip"
    assert got["Content-Disposition"] == 'attachment; filename="Friends 1.mrpack"'
    assert got["Content-Security-Policy"] == "default-src 'none'"
    ctx = next(iter(client.app.state.core.servers.values()))
    assert body == modpackmake.file_path(ctx, made["token"]).read_bytes()


def test_the_link_sends_nothing_else(linked):
    client, headers = linked
    make(client, headers)
    link = switch(client, headers, True).json()["modpack_url"]
    base = link.rsplit("/", 2)[0]
    token = link.rsplit("/", 2)[1]
    assert fetch(f"{base}/{'0' * 32}/modpack.mrpack")[0] == 404  # wrong token
    assert fetch(f"{base}/{token}/server.properties")[0] == 404
    assert fetch(f"{base}/{token}/../config/private.json")[0] == 404
    assert fetch(f"{base}/{token}/resource-pack.zip")[0] == 404  # no pack yet
    assert fetch(f"{base}/")[0] == 404
    assert fetch(link, method="POST")[0] == 405


def test_new_link_and_off_stop_the_old_link_at_once(linked):
    client, headers = linked
    make(client, headers)
    old = switch(client, headers, True).json()["modpack_url"]
    new = client.post(api(client, "download-link/new"), headers=headers).json()["modpack_url"]
    assert new != old
    assert fetch(old)[0] == 404 and fetch(new)[0] == 200
    off = switch(client, headers, False).json()
    assert off["on"] is False and off["modpack_url"] is None and not off["listening"]
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(new, timeout=2)
    # On again: another new link, never the old one back.
    again = switch(client, headers, True).json()["modpack_url"]
    assert again not in (old, new)


def test_only_the_owner_turns_the_link_on(linked):
    client, owner = linked
    sam = moderator(client, owner, {"server.view", "mods.manage", "settings.game"})
    assert client.get(api(client, "download-link"), headers=sam).status_code == 200
    assert switch(client, sam, True).status_code == 403
    assert client.post(api(client, "download-link/new"), headers=sam).status_code == 403


def test_one_address_gets_two_downloads_at_once_and_thirty_asks_a_minute():
    hub = downloadlink.LinkServer(core=None)
    assert hub._allowed("1.2.3.4")
    hub.active["1.2.3.4"] = downloadlink.PER_ADDRESS
    assert not hub._allowed("1.2.3.4")
    assert hub._allowed("5.6.7.8")  # someone else still gets in
    hub.active.clear()
    for _ in range(downloadlink.ASKS_A_MINUTE):
        assert hub._allowed("9.9.9.9")
    assert not hub._allowed("9.9.9.9")
    hub.active.update({f"10.0.0.{i}": 1 for i in range(downloadlink.IN_ALL)})
    assert not hub._allowed("11.1.1.1")  # 8 downloads in all


async def test_a_download_keeps_under_the_speed_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(downloadlink, "SPEED_CAP", downloadlink.CHUNK * 10)
    path = tmp_path / "pack.zip"
    path.write_bytes(b"x" * downloadlink.CHUNK * 4)

    class Writer:
        data = b""

        def write(self, chunk):
            self.data += chunk

        async def drain(self):
            pass

    writer = Writer()
    started = time.monotonic()
    await downloadlink._send(writer, path, "pack.zip")
    assert time.monotonic() - started >= 0.35  # 4 chunks at 10 chunks a second
    assert writer.data.endswith(path.read_bytes())


async def test_a_slow_asker_is_cut_off(tmp_path, monkeypatch):
    monkeypatch.setattr(downloadlink, "REQUEST_SECONDS", 0.2)

    class Core:
        servers: dict = {}

    hub = downloadlink.LinkServer(Core())
    server = await asyncio.start_server(hub._serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /")  # never finishes the request
    answer = await asyncio.wait_for(reader.read(), 3)
    assert answer.startswith(b"HTTP/1.1 400")
    assert not hub.active
    server.close()


# ---------------------------------------------------------------- the resource pack
def upload(client, headers, data: bytes, name: str = "Textures.zip", required: bool = False):
    return client.post(
        api(client, f"resource-pack/upload?required={str(required).lower()}"),
        files={"file": (name, data, "application/zip")},
        headers=headers,
    )


def test_a_resource_pack_is_served_by_the_link_and_written_in_server_properties(linked):
    client, headers = linked
    switch(client, headers, True)
    state = upload(client, headers, PACK, required=True).json()
    pack = state["resource_pack"]
    assert pack["sha1"] == hashlib.sha1(PACK).hexdigest() and pack["served"]
    assert pack["url"].endswith("/resource-pack.zip")
    values = props(client)
    assert values["resource-pack"] == pack["url"]
    assert values["resource-pack-sha1"] == pack["sha1"]
    assert values["require-resource-pack"] == "true"
    status, got, body = fetch(pack["url"])
    assert status == 200 and body == PACK
    assert got["Content-Disposition"] == 'attachment; filename="Textures.zip"'
    # A new link moves the pack with it; off takes it out of the game.
    new = client.post(api(client, "download-link/new"), headers=headers).json()
    assert props(client)["resource-pack"] == new["resource_pack"]["url"] != pack["url"]
    switch(client, headers, False)
    assert (
        props(client)["resource-pack"] == "" and props(client)["require-resource-pack"] == "false"
    )
    # Kept: on again, it's back.
    back = switch(client, headers, True).json()
    assert props(client)["resource-pack"] == back["resource_pack"]["url"]


@pytest.mark.parametrize(
    ("data", "words"),
    [
        (b"not a zip", "isn't a zip"),
        (zip_of({"assets/x.png": b"png"}), "pack.mcmeta"),
    ],
)
def test_only_a_real_resource_pack_is_taken(linked, data, words):
    client, headers = linked
    refused = upload(client, headers, data)
    assert refused.status_code == 400 and words in refused.json()["detail"]
    assert client.get(api(client, "download-link"), headers=headers).json()["resource_pack"] is None
    assert "resource-pack" not in props(client)


def test_an_outside_link_with_the_sites_sha1(linked):
    client, headers = linked
    sha1 = "a" * 40
    body = {"url": "https://packs.example.com/t.zip", "sha1": sha1.upper(), "required": False}
    state = client.post(api(client, "resource-pack"), json=body, headers=headers).json()
    assert state["resource_pack"]["url"] == "https://packs.example.com/t.zip"
    assert props(client)["resource-pack-sha1"] == sha1
    assert props(client)["require-resource-pack"] == "false"
    required = client.post(api(client, "resource-pack"), json={"required": True}, headers=headers)
    assert required.json()["resource_pack"]["required"] is True
    assert props(client)["require-resource-pack"] == "true"
    removed = client.delete(api(client, "resource-pack"), headers=headers).json()
    assert removed["resource_pack"] is None
    assert props(client)["resource-pack"] == "" and props(client)["resource-pack-sha1"] == ""


@pytest.mark.parametrize(
    ("body", "words"),
    [
        ({"url": "file:///C:/pack.zip"}, "https://"),
        ({"url": "https://x.com/a b.zip"}, "https://"),
        ({"url": "https://x.com/p.zip", "sha1": "abc"}, "40 letters"),
    ],
)
def test_a_bad_outside_link_is_refused(linked, body, words):
    client, headers = linked
    refused = client.post(api(client, "resource-pack"), json=body, headers=headers)
    assert refused.status_code == 400 and words in refused.json()["detail"]


def test_a_moderator_needs_game_settings_for_the_resource_pack(linked):
    client, owner = linked
    sam = moderator(client, owner, {"server.view", "mods.manage"})
    assert upload(client, sam, PACK).status_code == 403


def test_no_resource_pack_before_the_server_made_its_settings_file(server):
    client, headers = server
    path = client.app.state.core.mods.config.server_dir / "server.properties"
    path.unlink(missing_ok=True)
    refused = upload(client, headers, PACK)
    assert refused.status_code == 400 and "Start the server once" in refused.json()["detail"]


# ---------------------------------------------------------------- Discord
def test_too_big_for_discord_the_bot_posts_the_link_instead(linked, monkeypatch):
    client, headers = linked
    core = client.app.state.core
    ctx = next(iter(core.servers.values()))
    core.discord = bot = FakeBot(ctx.server_id)
    monkeypatch.setattr(modpackmake, "DISCORD_LIMIT", 10)
    link = switch(client, headers, True)
    made = make(client, headers).json()
    assert made["discord"] is True
    posted = client.post(
        api(client, "modpack/made/discord"), json={"token": made["token"]}, headers=headers
    ).json()
    assert posted["as_link"] is True
    _, _, body, files = bot.posts[0]
    assert files is None
    url = client.get(api(client, "download-link"), headers=headers).json()["modpack_url"]
    assert f"<{url}>" in body["content"]
    assert link.status_code == 200
