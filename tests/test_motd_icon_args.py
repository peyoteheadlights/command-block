# ruff: noqa: F811  (the owner fixture imported from test_helpers is used by name)
"""The MOTD's two lines and colours, the server icon, and the Java
arguments editor's checks."""

from __future__ import annotations

import base64
import struct
import zlib

import pytest

from agent.minecraft import properties
from agent.minecraft.java import jvm_args_problem
from agent.security import permissions as P

from .test_helpers import HELPER_PASSWORD, add_helper, owner, sign_in  # noqa: F401


def png(width: int = 64, height: int = 64, colour: bytes = b"\x10\x20\x30") -> bytes:
    """A real, tiny PNG of one colour."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + colour * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def put_icon(client, data: bytes, headers=None):
    body = {"png": base64.b64encode(data).decode()}
    return client.put("/api/servers/survival/server-icon", json=body, headers=headers)


# ---------------------------------------------------------------- the MOTD
def test_the_motd_can_be_two_lines_with_colours(multi_client, multi):
    folder = multi.for_server("survival").server_dir
    motd = "§6§lGold Server\n§7come say hi"
    response = multi_client.put(
        "/api/servers/survival/game-settings", json={"values": {"motd": motd}}
    )
    assert response.status_code == 200, response.text
    text = (folder / "server.properties").read_text(encoding="utf-8")
    # Written the way Minecraft reads it: \u escapes for § and the line break.
    assert "motd=\\u00A76\\u00A7lGold Server\\u000A\\u00A77come say hi" in text
    view = multi_client.get("/api/servers/survival/game-settings").json()
    field = next(f for f in view["fields"] if f["key"] == "motd")
    assert field["value"] == motd and field["lines"] == 2


def test_other_text_settings_stay_one_line():
    bedrock = properties.fields_for("bedrock")["server-name"]
    with pytest.raises(properties.PropertiesError, match="one line"):
        properties.check_value(bedrock, "two\nlines")


# ---------------------------------------------------------------- server icon
def test_an_icon_is_saved_read_back_and_undone(multi_client, multi):
    folder = multi.for_server("survival").server_dir
    assert multi_client.get("/api/servers/survival/server-icon").json()["png"] is None
    assert multi_client.get("/api/servers/survival/game-settings").json()["icon"] is False

    first = png()
    assert put_icon(multi_client, first).status_code == 200
    assert (folder / "server-icon.png").read_bytes() == first
    shown = multi_client.get("/api/servers/survival/server-icon").json()["png"]
    assert base64.b64decode(shown) == first
    assert multi_client.get("/api/servers/survival/game-settings").json()["icon"] is True

    # A second icon keeps the first in a backup, which Undo puts back.
    changed = put_icon(multi_client, png(colour=b"\x40\x50\x60"))
    assert changed.status_code == 200
    assert changed.json()["undo"]["kind"] == "restore_backup"
    assert multi_client.post("/api/servers/survival/changes/undo", json={}).status_code == 200
    assert (folder / "server-icon.png").read_bytes() == first

    removed = multi_client.delete("/api/servers/survival/server-icon")
    assert removed.status_code == 200 and removed.json()["icon"] is False
    assert not (folder / "server-icon.png").exists()
    assert removed.json()["undo"]["kind"] == "restore_backup"
    assert multi_client.post("/api/servers/survival/changes/undo", json={}).status_code == 200
    assert (folder / "server-icon.png").read_bytes() == first


@pytest.mark.parametrize(
    "data, words",
    [
        (png(128, 128), "64 x 64"),
        (png(64, 32), "This one is 64 x 32"),
        (b"GIF89a not a png at all" * 3, "isn't a PNG"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 30 + b"x" * (260 * 1024), "too big"),
    ],
    # Short names: pytest would put the whole picture in the test's name.
    ids=["128x128", "64x32", "gif", "too-big"],
)
def test_a_wrong_picture_is_refused_and_nothing_is_written(multi_client, multi, data, words):
    folder = multi.for_server("survival").server_dir
    response = put_icon(multi_client, data)
    assert response.status_code == 400
    assert words in response.text
    assert not (folder / "server-icon.png").exists()


def test_broken_base64_is_refused(multi_client):
    response = multi_client.put("/api/servers/survival/server-icon", json={"png": "not base64!"})
    assert response.status_code == 400


def test_a_moderator_needs_the_game_settings_switch(multi_client, owner):
    sam = add_helper(multi_client, owner)
    body = {"grants": {"survival": sorted(P.START_AND_STOP)}}
    assert multi_client.put("/api/accounts/sam", json=body, headers=owner).status_code == 200
    sam = sign_in(multi_client, "sam", HELPER_PASSWORD)
    assert put_icon(multi_client, png(), headers=sam).status_code == 403


def test_bedrock_shows_no_icon(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from agent.main import create_app
    from agent.security.auth import hash_password

    from .conftest import PASSWORD, build_bedrock_config

    monkeypatch.setenv("MCSC_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MCSC_ADMIN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))
    monkeypatch.delenv("MCSC_API_TOKEN", raising=False)
    with TestClient(create_app(build_bedrock_config(tmp_path))) as client:
        headers = sign_in(client, "admin", PASSWORD)
        view = client.get("/api/servers/bedrock/game-settings", headers=headers).json()
        assert view["icon"] is None
        body = {"png": base64.b64encode(png()).decode()}
        refused = client.put("/api/servers/bedrock/server-icon", json=body, headers=headers)
        assert refused.status_code == 400 and "Bedrock" in refused.text


# ---------------------------------------------------------------- Java arguments
@pytest.mark.parametrize(
    "args, words",
    [
        (["-Xmx4G", "nogui"], "'nogui' isn't a Java argument"),
        (["@args.txt"], "isn't a Java argument"),
        (["-jar", "other.jar"], "-jar can't be set here"),
        (["-cp", "x"], "-cp can't be set here"),
        ("-Xmx4G", "list of words"),
        (["-X"] * 101, "more than 100"),
    ],
)
def test_arguments_that_break_the_launch_are_refused_for_everyone(args, words):
    assert words in (jvm_args_problem(args, owner=True) or "")


@pytest.mark.parametrize(
    "arg",
    [
        "-javaagent:C:\\x\\agent.jar",
        "-agentpath:C:\\x\\a.dll",
        "-XX:OnOutOfMemoryError=calc.exe",
        "-XX:OnError=cmd /c del",
        "-Djava.library.path=C:\\x",
    ],
)
def test_arguments_that_run_other_code_are_the_owners_alone(arg):
    assert jvm_args_problem(["-Xmx4G", arg], owner=True) is None
    assert "Only the owner" in (jvm_args_problem(["-Xmx4G", arg], owner=False) or "")


def test_normal_flags_pass():
    aikar = ["-Xms4G", "-Xmx4G", "-XX:+UseG1GC", "-XX:MaxGCPauseMillis=200", "-Dusing.aikars=true"]
    assert jvm_args_problem(aikar, owner=False) is None


def test_a_moderator_cant_save_an_agent_flag_but_the_owner_can(multi_client, owner, multi):
    add_helper(multi_client, owner)
    body = {"grants": {"survival": sorted(P.RUN_IT)}}
    assert multi_client.put("/api/accounts/sam", json=body, headers=owner).status_code == 200
    sam = sign_in(multi_client, "sam", HELPER_PASSWORD)
    flags = ["-Xmx2G", "-XX:OnOutOfMemoryError=calc.exe"]
    update = {"updates": {"server.jvm_args": flags}}
    said = multi_client.put("/api/servers/survival/settings", json=update, headers=sam).json()
    assert "Only the owner" in said["rejected"]["server.jvm_args"]
    assert multi.for_server("survival").server.jvm_args != flags
    said = multi_client.put("/api/servers/survival/settings", json=update, headers=owner).json()
    assert said["applied"]["server.jvm_args"] == flags
