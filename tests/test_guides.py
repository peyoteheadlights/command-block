"""Add-on guides and Edit settings.

Guides: every shipped guide file passes the schema check, a guide shows only
for its add-on in its version range, a button sends exactly the command its
checked fields build, through the console checks, and returns what the
(fake) console said. Edit settings: only text files in the server folder,
checked as their format before anything is written, one backup and one
Undo per save, hidden values that stay the file's own.
"""

from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import configfiles, guides
from agent.main import create_app
from agent.security import permissions as P
from agent.security.auth import hash_password

from .conftest import PASSWORD, build_multi_config
from .test_helpers import add_helper
from .test_moderators import give

BASE = "/api/servers/survival"


def plugin_jar(folder: Path, name: str, version: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}-{version}.jar"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("plugin.yml", f"name: {name}\nversion: '{version}'\nmain: x.Main\n")
        zf.writestr("x/Main.class", b"\xca\xfe\xba\xbe")
    return path


@pytest.fixture
def paper(tmp_path, monkeypatch):
    """The real agent with two Paper servers (the fake Minecraft server)."""
    config = build_multi_config(tmp_path)
    for server in config._data["servers"]:
        server.update(type="paper")
    config._server_cache.clear()
    monkeypatch.setenv("MCSC_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MCSC_ADMIN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))
    monkeypatch.delenv("MCSC_API_TOKEN", raising=False)
    with TestClient(create_app(config)) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        client.headers["Authorization"] = f"Bearer {token.json()['token']}"
        yield client


def ctx_of(client, server_id="survival"):
    return client.app.state.core.servers[server_id]


def install(client, name="Chunky", version="1.4.40") -> Path:
    ctx = ctx_of(client)
    path = plugin_jar(ctx.config.mods_dir, name, version)
    ctx.mods._cache = None
    return path


def start(client) -> None:
    assert client.post(f"{BASE}/server/start").status_code == 200
    for _ in range(200):
        if client.get(f"{BASE}/status").json()["state"] == "ONLINE":
            return
        time.sleep(0.1)
    raise AssertionError("the fake server didn't start")


def press(client, button, values=None, confirm=False, headers=None, guide="chunky"):
    return client.post(
        f"{BASE}/guides/{guide}/{button}",
        json={"values": values or {}, "confirm": confirm},
        headers=headers,
    )


# ------------------------------------------------------------ the guide files
def test_every_shipped_guide_passes_the_schema():
    files = sorted(guides.FOLDER.glob("*.json"))
    loaded = guides.load()
    assert len(files) == len(loaded) == 25
    for path in files:
        guides.check_guide(json.loads(path.read_text("utf-8")), path.stem)
    # Two guides for one add-on would show twice.
    names = [n.lower() for g in loaded.values() for n in g.names]
    assert len(names) == len(set(names))


def broken(**change):
    data = json.loads((guides.FOLDER / "chunky.json").read_text("utf-8"))
    button = data["buttons"][0]
    for key, value in change.items():
        button[key] = value
    return data


@pytest.mark.parametrize(
    "change, words",
    [
        ({"command": "chunky start {world} square 0 0 {size}"}, "fields and command"),
        ({"command": "chunky start {world} {radius}; stop"}, "characters"),
        ({"changes_world": False, "command": "kill {world} {radius}"}, "changes_world"),
        ({"permission": "users.manage"}, "permission"),
        (
            {
                "fields": [
                    {"id": "world", "kind": "file", "label": "World"},
                    {"id": "radius", "kind": "number", "label": "R", "min": 1, "max": 9},
                ]
            },
            "unknown kind",
        ),
    ],
)
def test_a_broken_guide_is_refused(change, words):
    with pytest.raises(guides.GuideError, match=words):
        guides.check_guide(broken(**change), "chunky")


def test_a_guide_without_a_usable_version_range_is_refused():
    data = broken()
    data["versions"] = "whenever"
    with pytest.raises(guides.GuideError, match="range"):
        guides.check_guide(data, "chunky")


# ------------------------------------------------------------ which guides show
def shown(client, headers=None) -> list[dict]:
    response = client.get(f"{BASE}/guides", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["guides"]


def test_a_guide_shows_for_its_installed_add_on(paper):
    assert shown(paper) == []
    install(paper)
    found = shown(paper)
    assert [g["id"] for g in found] == ["chunky"]
    start_button = found[0]["buttons"][0]
    assert start_button["changes_world"] and start_button["warning"]
    assert start_button["fields"][0]["options"] == [
        {"value": w, "label": w} for w in ("world", "world_nether", "world_the_end")
    ] or start_button["fields"][0]["options"] == [{"value": "world", "label": "world"}]


@pytest.mark.parametrize("version", ["2.0.0", "1.2.9", "custom-build"])
def test_a_version_outside_the_range_hides_the_guide(paper, version):
    install(paper, version=version)
    assert shown(paper) == []


def test_a_turned_off_add_on_has_no_guide(paper):
    path = install(paper)
    path.rename(path.with_name(path.name + ".disabled"))
    ctx_of(paper).mods._cache = None
    assert shown(paper) == []


def test_mod_servers_get_no_plugin_guides(multi_client):
    ctx = ctx_of(multi_client)
    plugin_jar(ctx.config.mods_dir, "Chunky", "1.4.40")
    assert multi_client.get(f"{BASE}/guides").json()["guides"] == []


# ------------------------------------------------------------ pressing buttons
def test_a_button_sends_its_command_and_returns_the_console_answer(paper):
    install(paper)
    start(paper)
    answer = press(paper, "progress")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["command"] == "chunky progress"
    assert body["answered"]
    assert any("[Console] chunky progress" in line["raw"] for line in body["lines"])
    assert not any(line["raw"].startswith(">") for line in body["lines"])


def test_typed_fields_fill_the_command_and_a_world_change_needs_confirm(paper):
    install(paper)
    start(paper)
    values = {"world": "world", "radius": 2500}
    refused = press(paper, "start", values)
    assert refused.status_code == 400 and "confirmed" in refused.json()["detail"]
    sent = press(paper, "start", values, confirm=True)
    assert sent.status_code == 200, sent.text
    assert sent.json()["command"] == "chunky start world square 0 0 2500"


@pytest.mark.parametrize(
    "values",
    [
        {"world": "world", "radius": 50},
        {"world": "world", "radius": 40000},
        {"world": "world", "radius": "2000; stop"},
        {"world": "world", "radius": True},
        {"world": "../../x", "radius": 2000},
        {"world": "world"},
    ],
)
def test_a_bad_field_value_is_refused_and_nothing_is_sent(paper, values):
    install(paper)
    start(paper)
    ctx = ctx_of(paper)
    before = len(ctx.server.console)
    refused = press(paper, "start", values, confirm=True)
    assert refused.status_code == 400
    assert not any(
        line.raw.startswith("> chunky")
        for line in ctx.server.console.tail(len(ctx.server.console) - before + 1)
    )


def test_a_stopped_server_is_said_plainly(paper):
    install(paper)
    refused = press(paper, "progress")
    assert refused.status_code == 409
    assert "running" in refused.json()["detail"]


def test_a_guide_for_an_add_on_not_here_cant_be_pressed(paper):
    start(paper)
    assert press(paper, "progress").status_code == 400


def test_a_moderator_without_the_console_sees_the_guide_but_no_buttons(paper):
    owner = dict(paper.headers)
    paper.headers.pop("Authorization")
    sam = add_helper(paper, owner)
    give(paper, owner, {"survival": P.LOOK_AFTER_IT})
    install(paper)
    start_headers = owner
    assert paper.post(f"{BASE}/server/start", headers=start_headers).status_code == 200
    found = shown(paper, sam)
    assert found and found[0]["buttons"] == []
    assert press(paper, "progress", headers=sam).status_code == 403


# ------------------------------------------------------------ Edit settings
def write(ctx, rel: str, text: str | bytes) -> Path:
    path = ctx.config.server_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    return path


CHUNKY_CONFIG = "language: en\ncontinue-on-restart: false\n"
DISCORD_CONFIG = (
    "# DiscordSRV\n"
    'BotToken: "abc.def.ghi"\n'
    'Channels: {"global": "123"}\n'
    "Database:\n"
    "  password: hunter2\n"
    "  user: steve\n"
)


def opened(client, rel: str, headers=None) -> dict:
    response = client.get(f"{BASE}/config-files/file", params={"path": rel}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def save(client, rel: str, text: str, version: str, headers=None):
    return client.post(
        f"{BASE}/config-files/save",
        json={"path": rel, "text": text, "version": version},
        headers=headers,
    )


def test_the_server_folder_lists_its_config_files_but_not_its_own_pages(paper):
    ctx = ctx_of(paper)
    write(ctx, "bukkit.yml", "settings: {}\n")
    write(ctx, "config/paper-global.yml", "a: 1\n")
    write(ctx, "plugins/Chunky/config.yml", CHUNKY_CONFIG)
    write(ctx, "world/stats/x.json", "{}")
    write(ctx, "logs/old.txt", "log")
    write(ctx, "whitelist.json", "[]")
    write(ctx, "start.bat", "java -jar x.jar")
    write(ctx, "plugins/Chunky/config.yml.replaced-20261031-000000", "old")
    paths = [f["path"] for f in paper.get(f"{BASE}/config-files").json()["files"]]
    assert "bukkit.yml" in paths
    assert "config/paper-global.yml" in paths
    assert "plugins/Chunky/config.yml" in paths
    for left_out in ("server.properties", "whitelist.json", "world/stats/x.json", "logs/old.txt"):
        assert left_out not in paths
    assert not [p for p in paths if p.endswith((".bat", "000000"))]


def test_edit_settings_on_an_add_on_lists_its_own_files(paper):
    ctx = ctx_of(paper)
    jar = install(paper)
    write(ctx, "plugins/Chunky/config.yml", CHUNKY_CONFIG)
    write(ctx, "plugins/Other/config.yml", "x: 1\n")
    found = paper.get(f"{BASE}/config-files", params={"addon": jar.name}).json()
    assert [f["path"] for f in found["files"]] == ["plugins/Chunky/config.yml"]


def test_a_mod_server_lists_a_mods_files_in_config(multi_client):
    from .test_mods import make_jar

    ctx = ctx_of(multi_client)
    make_jar(ctx.config.mods_dir / "lithium.jar", "lithium")
    write(ctx, "config/lithium.properties", "mixin.ai=true\n")
    write(ctx, "config/sodium-options.json", "{}")
    found = multi_client.get(f"{BASE}/config-files", params={"addon": "lithium.jar"}).json()
    assert [f["path"] for f in found["files"]] == ["config/lithium.properties"]


def test_a_save_is_checked_backed_up_and_undone_on_its_own(paper):
    ctx = ctx_of(paper)
    jar = install(paper)
    path = write(ctx, "plugins/Chunky/config.yml", CHUNKY_CONFIG)
    other = write(ctx, "plugins/Chunky/tasks.yml", "kept: true\n")
    first = opened(paper, "plugins/Chunky/config.yml")
    changed = CHUNKY_CONFIG.replace("false", "true")
    done = save(paper, "plugins/Chunky/config.yml", changed, first["version"])
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["undo"] and body["safety_backup"]
    assert path.read_text("utf-8") == changed
    # Something else changes in the plugin folder after the save.
    other.write_text("kept: changed\n", encoding="utf-8")
    undo = paper.post(f"{BASE}/changes/undo", json={})
    assert undo.status_code == 200, undo.text
    assert path.read_text("utf-8") == CHUNKY_CONFIG
    assert other.read_text("utf-8") == "kept: changed\n"
    assert jar.is_file()


def test_broken_yaml_is_refused_with_its_line_and_nothing_is_written(paper):
    ctx = ctx_of(paper)
    path = write(ctx, "plugins/Chunky/config.yml", CHUNKY_CONFIG)
    first = opened(paper, "plugins/Chunky/config.yml")
    refused = save(paper, "plugins/Chunky/config.yml", "language: en\n  bad: [\n", first["version"])
    assert refused.status_code == 400
    assert refused.json()["line"] in (2, 3)
    assert path.read_text("utf-8") == CHUNKY_CONFIG


@pytest.mark.parametrize(
    "kind, text, line",
    [
        ("json", '{\n  "a": 1,\n  "b": \n}', 4),
        ("toml", 'a = 1\nb = "x\n', 2),
        ("yaml", "a: 1\nb: [1, 2\n", 3),
    ],
)
def test_each_format_says_the_line(kind, text, line):
    with pytest.raises(configfiles.ConfigFileError) as caught:
        configfiles.check(text, kind)
    assert caught.value.line == line


def test_a_file_changed_on_the_pc_since_it_was_opened_is_refused(paper):
    ctx = ctx_of(paper)
    path = write(ctx, "bukkit.yml", "a: 1\n")
    first = opened(paper, "bukkit.yml")
    path.write_text("a: 2\n", encoding="utf-8")
    refused = save(paper, "bukkit.yml", "a: 3\n", first["version"])
    assert refused.status_code == 409
    assert path.read_text("utf-8") == "a: 2\n"


def test_hidden_values_stay_hidden_and_saving_keeps_them(paper):
    ctx = ctx_of(paper)
    rel = "plugins/DiscordSRV/config.yml"
    path = write(ctx, rel, DISCORD_CONFIG)
    first = opened(paper, rel)
    assert "abc.def.ghi" not in first["text"] and "hunter2" not in first["text"]
    assert {h["key"] for h in first["hidden"]} == {"BotToken", "password"}
    assert 'BotToken: "••••••"' in first["text"] and "user: steve" in first["text"]
    done = save(paper, rel, first["text"].replace("steve", "alex"), first["version"])
    assert done.status_code == 200, done.text
    assert path.read_text("utf-8") == DISCORD_CONFIG.replace("steve", "alex")


def test_showing_hidden_values_is_logged(paper):
    ctx = ctx_of(paper)
    rel = "plugins/DiscordSRV/config.yml"
    write(ctx, rel, DISCORD_CONFIG)
    shown_file = paper.post(f"{BASE}/config-files/show", json={"path": rel})
    assert shown_file.status_code == 200
    assert "hunter2" in shown_file.json()["text"]
    rows = ctx.core.db.query(
        "SELECT action, target FROM audit_log WHERE action = 'config_file_show'"
    )
    assert [r["target"] for r in rows] == [rel]


def test_a_hidden_value_that_was_edited_around_is_refused(paper):
    ctx = ctx_of(paper)
    rel = "plugins/DiscordSRV/config.yml"
    path = write(ctx, rel, DISCORD_CONFIG)
    first = opened(paper, rel)
    edited = first["text"].replace('BotToken: "••••••"', 'BotToken: "x••••••"')
    refused = save(paper, rel, edited, first["version"])
    assert refused.status_code == 400 and refused.json()["line"] == 2
    assert path.read_text("utf-8") == DISCORD_CONFIG


@pytest.mark.parametrize(
    "key, secret",
    [
        ("BotToken", True),
        ("api_key", True),
        ("password", True),
        ("client-secret", True),
        ("keep-alive", False),
        ("monkey", False),
        ("tokens-per-player", False),
    ],
)
def test_which_keys_are_hidden(key, secret):
    assert configfiles.is_secret(key) is secret


@pytest.mark.parametrize(
    "rel",
    [
        "../outside.yml",
        "plugins/../../outside.yml",
        "/etc/passwd.txt",
        "C:/x.yml",
        "server.properties",
        "start.bat",
    ],
)
def test_paths_outside_the_folder_or_on_their_own_page_are_refused(paper, rel):
    response = paper.get(f"{BASE}/config-files/file", params={"path": rel})
    assert response.status_code == 400


@pytest.mark.skipif(os.name == "nt", reason="links need admin rights on Windows")
def test_a_link_out_of_the_folder_is_refused(paper, tmp_path):
    ctx = ctx_of(paper)
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "keys.yml").write_text("a: 1\n", encoding="utf-8")
    (ctx.config.server_dir / "plugins").mkdir(exist_ok=True)
    (ctx.config.server_dir / "plugins" / "Linked").symlink_to(outside)
    response = paper.get(f"{BASE}/config-files/file", params={"path": "plugins/Linked/keys.yml"})
    assert response.status_code == 400
    listed = [f["path"] for f in paper.get(f"{BASE}/config-files").json()["files"]]
    assert "plugins/Linked/keys.yml" not in listed


def test_big_and_binary_files_dont_open(paper):
    ctx = ctx_of(paper)
    write(ctx, "big.txt", "x" * (configfiles.MAX_BYTES + 1))
    write(ctx, "odd.json", b"{\x00}")
    write(ctx, "latin.txt", "caf\xe9".encode("latin-1"))
    for rel in ("big.txt", "odd.json", "latin.txt"):
        assert paper.get(f"{BASE}/config-files/file", params={"path": rel}).status_code == 400


def test_a_running_server_is_told_to_restart(paper):
    ctx = ctx_of(paper)
    write(ctx, "bukkit.yml", "a: 1\n")
    start(paper)
    first = opened(paper, "bukkit.yml")
    done = save(paper, "bukkit.yml", "a: 2\n", first["version"]).json()
    assert done["running"] and done["restart_needed"]


def test_windows_line_endings_and_a_bom_are_kept(paper):
    ctx = ctx_of(paper)
    path = write(ctx, "bukkit.yml", b"\xef\xbb\xbfa: 1\r\nb: 2\r\n")
    first = opened(paper, "bukkit.yml")
    assert first["text"] == "a: 1\r\nb: 2\r\n"
    assert save(paper, "bukkit.yml", "a: 1\nb: 3\n", first["version"]).status_code == 200
    assert path.read_bytes() == b"\xef\xbb\xbfa: 1\r\nb: 3\r\n"


def test_a_moderator_without_change_mods_cant_read_the_files(paper):
    ctx = ctx_of(paper)
    write(ctx, "bukkit.yml", "a: 1\n")
    owner = dict(paper.headers)
    paper.headers.pop("Authorization")
    sam = add_helper(paper, owner)
    give(paper, owner, {"survival": P.RUN_IT - {P.MODS_MANAGE}})
    assert paper.get(f"{BASE}/config-files", headers=sam).status_code == 403
    response = paper.get(f"{BASE}/config-files/file", params={"path": "bukkit.yml"}, headers=sam)
    assert response.status_code == 403
