"""Console friends: the MCXboxBroadcast add-on for Geyser (agent/consolefriends.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent import consolefriends
from agent.backups.manager import BackupManager
from agent.minecraft.console import parse_line
from agent.security.auth import hash_password

from .conftest import build_config

PASSWORD = "correct horse battery staple"
URL = "/api/servers/test/crossplay/console-friends"
CODE_LINE = (
    "[12:00:01] [Server thread/INFO]: [mcxboxbroadcast] [Auth] To sign in, use a web browser "
    "to open the page https://www.microsoft.com/link and enter the code ABCD1234 to authenticate."
)


@pytest.fixture
def downloaded(monkeypatch):
    """The add-on "downloaded": the file written, nothing fetched."""
    asked = []

    async def download(folder, repo, tag, filename, name):
        asked.append((repo, tag, filename))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"PK fake jar")
        return folder / name

    monkeypatch.setattr(consolefriends, "download", download)
    return asked


@pytest.fixture
def client(tmp_path, monkeypatch, downloaded):
    monkeypatch.setenv("MCSC_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MCSC_ADMIN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))
    monkeypatch.delenv("MCSC_API_TOKEN", raising=False)
    from agent.main import create_app

    consolefriends._heard.clear()
    with TestClient(create_app(build_config(tmp_path))) as c:
        token = c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        c.headers["Authorization"] = f"Bearer {token.json()['token']}"
        yield c


def ctx(c):
    return c.app.state.core.servers["test"]


def crossplay_on(c) -> None:
    ctx(c).config.set("server.crossplay", True)


def test_needs_crossplay_first(client, downloaded):
    found = client.get("/api/servers/test/crossplay").json()["console_friends"]
    assert found == {
        "available": False,
        "on": False,
        "sign_in": None,
        "gamertag": None,
        "problem": None,
    }
    answer = client.put(URL, json={"enabled": True, "accept_risks": True})
    assert answer.status_code == 409
    assert "Let Bedrock players join" in answer.json()["detail"]
    assert downloaded == []


def test_refused_until_the_risks_are_accepted(client, downloaded):
    crossplay_on(client)
    answer = client.put(URL, json={"enabled": True})
    assert answer.status_code == 409
    assert "risks" in answer.json()["detail"]
    assert downloaded == []


def test_on_puts_the_add_on_in_geysers_extensions_folder(client, downloaded):
    crossplay_on(client)
    answer = client.put(URL, json={"enabled": True, "accept_risks": True})
    assert answer.status_code == 200, answer.text
    assert answer.json()["console_friends"]["on"] is True
    assert downloaded == [(consolefriends.REPO, None, consolefriends.JAR)]
    folder = consolefriends.folder(ctx(client))
    assert folder is not None
    assert folder.parts[-3:] == ("config", "Geyser-Fabric", "extensions")
    assert (folder / consolefriends.JAR).is_file()


def test_off_deletes_the_add_on_and_its_microsoft_sign_in(client):
    crossplay_on(client)
    client.put(URL, json={"enabled": True, "accept_risks": True})
    folder = consolefriends.folder(ctx(client))
    assert folder is not None
    (folder / "mcxboxbroadcast").mkdir()
    (folder / "mcxboxbroadcast" / "cache.json").write_text("{}", encoding="utf-8")
    answer = client.put(URL, json={"enabled": False})
    assert answer.status_code == 200, answer.text
    assert answer.json()["console_friends"]["on"] is False
    assert not (folder / consolefriends.JAR).exists()
    assert not (folder / "mcxboxbroadcast").exists()


def test_the_console_gives_the_code_then_the_gamertag(client, monkeypatch):
    server = ctx(client)
    crossplay_on(client)
    client.put(URL, json={"enabled": True, "accept_risks": True})
    # The code only shows while the server runs; no real start needed.
    monkeypatch.setattr(type(server.server), "running", property(lambda _s: True))
    consolefriends.heard(server, parse_line(CODE_LINE, 1))
    found = consolefriends.status(server, owner=True)
    assert found["sign_in"] == {"url": "https://www.microsoft.com/link", "code": "ABCD1234"}
    # Whoever types the code links their own account: owner only.
    assert consolefriends.status(server, owner=False)["sign_in"] is None
    consolefriends.heard(
        server,
        parse_line(
            "[12:00:05] [Server thread/INFO]: [mcxboxbroadcast] [Primary Session] "
            "Successfully authenticated as SpareTag (2535400000000000) with 0/1000 friends",
            2,
        ),
    )
    found = consolefriends.status(server, owner=True)
    assert found["sign_in"] is None
    assert found["gamertag"] == "SpareTag"


def test_chat_and_other_pages_never_pass_for_the_sign_in(client):
    server = ctx(client)
    for line in (
        # a player typing the line in chat
        "[12:00:01] [Server thread/INFO]: <Steve> " + CODE_LINE.split(": ", 1)[1],
        # not Microsoft's page
        CODE_LINE.replace("https://www.microsoft.com/link", "https://evil.example/link"),
    ):
        consolefriends.heard(server, parse_line(line, 1))
    assert not consolefriends._heard.get("test", {}).get("sign_in")


def test_paper_lines_have_geysers_own_tag_in_front(client):
    server = ctx(client)
    line = (
        "[12:00:01 INFO]: [Geyser-Spigot] [mcxboxbroadcast] [Auth] To sign in, use a web browser "
        "to open the page https://www.microsoft.com/link and enter the code WXYZ9876 to authenticate."
    )
    consolefriends.heard(server, parse_line(line, 1))
    assert consolefriends._heard["test"]["sign_in"]["code"] == "WXYZ9876"


def test_backups_leave_the_microsoft_sign_in_out(tmp_path):
    folder = tmp_path / "config" / "Geyser-Fabric" / "extensions" / "mcxboxbroadcast"
    folder.mkdir(parents=True)
    (folder / "cache.json").write_text("{}", encoding="utf-8")
    (folder / "config.yml").write_text("a: 1", encoding="utf-8")
    files = BackupManager._collect([tmp_path])
    assert [f.name for f in files] == ["config.yml"]
