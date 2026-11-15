# ruff: noqa: F811  (the fixtures imported from other tests are used by name)
"""The Companion's title screen: the join button, the background picture
and the yellow flavor text the owner picks when making a modpack. They
travel in the modpack's players' half; nothing changes on the server."""

from __future__ import annotations

import json
import struct
import zipfile
import zlib

import pytest

from agent import companion, titlescreen

from .test_curseforge import MC, FakeCurseForge, fabric, fabric_jar, relay  # noqa: F401
from .test_modpack_make import api, make, saved, server, server_dir  # noqa: F401

ON = {
    "button": {"on": True, "address": "play.example.net:25570"},
    "flavor_text": {"on": True, "text": "Now with dragons!"},
    "background": {"on": False},
}


def png(width=32, height=32):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    rows = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def players_half(client, headers, made, tmp_path):
    with zipfile.ZipFile(saved(client, headers, made, tmp_path)) as zf:
        names = set(zf.namelist())
        return json.loads(zf.read(f"client-overrides/{companion.PLAYERS_FILE}")), names, zf


def upload(client, headers, data, name="picture.png"):
    return client.post(
        api(client, "modpack/title-background"),
        files={"file": (name, data, "image/png")},
        headers=headers,
    )


def options(client, headers):
    return client.get(api(client, "modpack/make"), headers=headers).json()


def test_the_button_and_flavor_text_go_in_the_players_half(server, tmp_path):
    client, headers = server
    (server_dir(client) / "server-icon.png").write_bytes(png(64, 64))
    made = make(client, headers, title_screen=ON).json()
    assert made["ok"], made
    got, names, _ = players_half(client, headers, made, tmp_path)
    assert got["format"] == 1, "old Companions still read the version"
    assert got["title_screen"] == {
        "name": next(iter(client.app.state.core.servers.values())).name,
        "address": "play.example.net:25570",
        "flavor_text": "Now with dragons!",
    }
    assert f"client-overrides/{titlescreen.ICON}" in names
    assert f"client-overrides/{titlescreen.BACKGROUND}" not in names
    # The next Make a modpack remembers it.
    assert options(client, headers)["title_screen"]["button"] == ON["button"]


def test_without_settings_nothing_changes_on_the_title_screen(server, tmp_path):
    client, headers = server
    made = make(client, headers).json()
    got, names, _ = players_half(client, headers, made, tmp_path)
    assert got["title_screen"]["address"] is None
    assert got["title_screen"]["flavor_text"] is None
    assert not [n for n in names if n.startswith("client-overrides/config/commandblock-companion/")]


def test_the_background_picture_is_uploaded_then_goes_in(server, tmp_path):
    client, headers = server
    assert options(client, headers)["title_screen"]["background_set"] is False
    picture = png(640, 360)
    got = upload(client, headers, picture)
    assert got.status_code == 200, got.text
    assert options(client, headers)["title_screen"]["background_set"] is True

    made = make(client, headers, title_screen={**ON, "background": {"on": True}}).json()
    with zipfile.ZipFile(saved(client, headers, made, tmp_path)) as zf:
        assert zf.read(f"client-overrides/{titlescreen.BACKGROUND}") == picture


@pytest.mark.parametrize(
    "data",
    [
        b"GIF89a not a png",
        png(8, 8),  # too small to be a background
        png(5000, 10),  # wider than Minecraft's textures go
    ],
)
def test_a_bad_background_is_refused(server, data):
    client, headers = server
    got = upload(client, headers, data)
    assert got.status_code == 400, got.text
    assert options(client, headers)["title_screen"]["background_set"] is False


def test_a_too_big_background_is_refused(server, monkeypatch):
    client, headers = server
    monkeypatch.setattr(titlescreen, "MAX_BACKGROUND_BYTES", 30)
    assert upload(client, headers, png(64, 64)).status_code == 400


def test_background_on_without_a_picture_is_refused(server):
    client, headers = server
    got = make(client, headers, title_screen={**ON, "background": {"on": True}})
    assert got.status_code == 400, got.text


@pytest.mark.parametrize(
    "button",
    [
        {"on": True, "address": ""},
        {"on": True, "address": "has space.net"},
        {"on": True, "address": "host:99999"},
        {"on": True, "address": "a\nb"},
    ],
)
def test_a_bad_address_is_refused(server, button):
    client, headers = server
    got = make(client, headers, title_screen={**ON, "button": button})
    assert got.status_code in (400, 422), got.text


@pytest.mark.parametrize("text", ["", "a" * 61, "two\nlines", "§ccolour codes"])
def test_a_bad_flavor_text_is_refused(server, text):
    client, headers = server
    got = make(client, headers, title_screen={**ON, "flavor_text": {"on": True, "text": text}})
    assert got.status_code in (400, 422), got.text


def test_a_changed_address_needs_a_new_version(server):
    client, headers = server
    assert make(client, headers, version="1", title_screen=ON).json()["ok"]
    moved = {**ON, "button": {"on": True, "address": "new.example.net"}}
    got = make(client, headers, version="1", title_screen=moved)
    assert got.status_code == 400 and "new version" in got.text
    assert make(client, headers, version="2", title_screen=moved).json()["ok"]


def test_the_discord_address_is_offered_first(server):
    client, headers = server
    from agent.discord import layout

    core = client.app.state.core
    layout.set_address(core.db, next(iter(core.servers)), "mc.example.org")
    assert options(client, headers)["title_screen"]["button"]["address"] == "mc.example.org"


def test_an_icon_that_is_not_a_small_png_is_left_out(server, tmp_path):
    client, headers = server
    (server_dir(client) / "server-icon.png").write_bytes(b"not a picture")
    made = make(client, headers, title_screen=ON).json()
    _, names, _ = players_half(client, headers, made, tmp_path)
    assert f"client-overrides/{titlescreen.ICON}" not in names
