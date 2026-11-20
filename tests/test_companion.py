# ruff: noqa: F811  (the fixtures imported from other tests are used by name)
"""The Companion's settings, written each time a modpack is made.

The server gets the check's settings (hashes of the players' mod files,
never anything from a player's PC); the modpack gets the players' half
(the modpack's name and version).
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile

import pytest

from agent import companion
from agent.mods import sides

from .test_curseforge import MC, FakeCurseForge, fabric, fabric_jar, relay  # noqa: F401
from .test_hangar_and_links import paper, plugin_jar, sites, zip_of  # noqa: F401
from .test_modpack_make import (
    OWN,
    SODIUM,
    ExportModrinth,
    api,
    make,
    saved,
    server,  # noqa: F401
    server_dir,
)


def settings(client, where=companion.MODS_FILE):
    return json.loads((server_dir(client) / where).read_text(encoding="utf-8"))


def test_a_made_modpack_writes_the_check_settings_on_the_server(server, relay):
    client, headers = server
    made = make(client, headers, version="1.4").json()
    assert made["ok"], made

    got = settings(client)
    assert got["format"] == 1
    assert got["required"] is False, "stays off until the join check is out"
    assert got["modpack"] == {"name": "Friends", "version": "1.4"}
    files = {f["path"]: f for f in got["files"]}
    # Every mod jar players load, also the one they must get themselves;
    # the server-only mod and the resource pack aren't mods players load.
    assert sorted(files) == [
        "mods/lithium.jar",
        "mods/ourtweaks.jar",
        "mods/secret.jar",
        "mods/sodium.jar",
    ]
    assert files["mods/sodium.jar"]["sha256"] == hashlib.sha256(SODIUM).hexdigest()
    assert files["mods/ourtweaks.jar"]["sha256"] == hashlib.sha256(OWN).hexdigest()
    assert files["mods/secret.jar"]["name"] == "Secret Mod"
    assert files["mods/sodium.jar"]["size"] == len(SODIUM)
    # The server's own copy, for spot checks: Both in mods/, Players only aside.
    assert files["mods/sodium.jar"]["on_server"] == "mods/sodium.jar"
    assert got["download_link"] is None
    assert got["chat_line"]["on"] is True and "Friends" in got["chat_line"]["text"]
    assert got["check"] == companion.CHECK


def test_the_modpack_carries_the_players_half(server, tmp_path):
    client, headers = server
    made = make(client, headers).json()
    with zipfile.ZipFile(saved(client, headers, made, tmp_path)) as zf:
        got = json.loads(zf.read(f"client-overrides/{companion.PLAYERS_FILE}"))
    # The modpack's mod jars: an update may replace only these, never the player's own.
    assert got["format"] == 1 and got["modpack"] == {"name": "Friends", "version": "1"}
    assert sorted(got["files"]) == sorted(f["path"] for f in settings(client)["files"])


def test_the_settings_file_is_never_offered_as_a_config_file_for_players(server):
    client, headers = server
    make(client, headers).json()
    options = client.get(api(client, "modpack/make"), headers=headers).json()
    assert companion.MODS_FILE not in [c["path"] for c in options["configs"]]


def test_a_new_modpack_version_replaces_the_settings(server):
    client, headers = server
    make(client, headers, version="1").json()
    (server_dir(client) / sides.FOLDER / "pretty.zip").unlink()
    (server_dir(client) / "mods" / "ourtweaks.jar").unlink()
    make(client, headers, version="2").json()
    got = settings(client)
    assert got["modpack"]["version"] == "2"
    assert "mods/ourtweaks.jar" not in {f["path"] for f in got["files"]}


def test_a_plugin_server_gets_it_in_the_plugins_folder(paper, monkeypatch):
    client, headers, folder = paper
    from agent.servertypes import versions

    class Plan:
        loader = "0.16.9"

    async def plan(type_id, minecraft, loader=None):
        return Plan()

    monkeypatch.setattr(versions, "make_plan", plan)
    client.app.state.core.mods.modrinth = ExportModrinth({"sodium": SODIUM})
    (folder / "Trees.jar").write_bytes(plugin_jar("Trees"))
    base = server_dir(client)
    (base / sides.FOLDER).mkdir()
    (base / sides.FOLDER / "sodium.jar").write_bytes(SODIUM)
    made = make(client, headers).json()
    assert made["ok"], made
    got = settings(client, companion.PLUGIN_FILE)
    assert [f["path"] for f in got["files"]] == ["mods/sodium.jar"], "plugins aren't player files"
    assert got["files"][0]["on_server"] == f"{sides.FOLDER}/sodium.jar"
    assert not (base / companion.MODS_FILE).exists()


@pytest.mark.skipif(os.name == "nt", reason="links need admin rights on Windows")
def test_a_linked_config_folder_is_never_written_through(server, tmp_path):
    client, headers = server
    base = server_dir(client)
    outside = tmp_path / "outside"
    outside.mkdir()
    for child in (base / "config").iterdir():
        child.unlink()
    (base / "config").rmdir()
    (base / "config").symlink_to(outside, target_is_directory=True)
    made = make(client, headers).json()
    assert made["ok"], made
    assert not list(outside.iterdir())


def test_too_many_files_writes_none_rather_than_a_cut_list(server, monkeypatch):
    client, headers = server
    make(client, headers, version="1").json()
    monkeypatch.setattr(companion, "MAX_FILES", 2)
    made = make(client, headers, version="2").json()
    assert made["ok"], made
    assert not (server_dir(client) / companion.MODS_FILE).exists(), "an old list would be wrong"


# ---------------------------------------------------------------- part 2: the join check
def with_companion(client):
    from .test_curseforge import fabric_jar

    folder = server_dir(client) / "mods"
    (folder / "companion.jar").write_bytes(fabric_jar(companion.MOD_ID, "0.1.0"))


def options(client, headers):
    return client.get(api(client, "modpack/make"), headers=headers).json()


def test_required_to_join_is_offered_only_with_the_companion_on_the_server(server):
    client, headers = server
    assert options(client, headers)["required_to_join"]["available"] is False
    refused = make(client, headers, required=True)
    assert refused.status_code == 400
    assert "Companion" in refused.json()["detail"]

    with_companion(client)
    got = options(client, headers)
    assert got["required_to_join"] == {"available": True, "on": False, "needs_fabric": False}
    assert got["chat_line"] == {"on": True, "text": companion.CHAT_LINE_DEFAULT}


def test_a_required_modpack_says_so_and_carries_the_companion(server, tmp_path):
    client, headers = server
    with_companion(client)
    # Even when the owner marked it server-only, players need it for the check.
    marks = server_dir(client) / sides.MARKS_FILE
    marks.write_text(json.dumps({"ledger": "server", companion.MOD_ID: "server"}))
    made = make(client, headers, required=True).json()
    assert made["ok"], made
    got = settings(client)
    assert got["required"] is True
    assert "mods/companion.jar" in {f["path"] for f in got["files"]}
    with zipfile.ZipFile(saved(client, headers, made, tmp_path)) as zf:
        assert "overrides/mods/companion.jar" in zf.namelist()
    # The next Make a modpack remembers it.
    assert options(client, headers)["required_to_join"]["on"] is True


def test_the_owner_rewords_or_turns_off_the_chat_line(server):
    client, headers = server
    with_companion(client)
    make(client, headers, chat_line={"on": True, "text": "Grab {name} {version} first! {link}"})
    got = settings(client)
    assert got["chat_line"] == {"on": True, "text": "Grab Friends 1 first! "}
    assert options(client, headers)["chat_line"]["text"] == "Grab {name} {version} first! {link}"

    make(client, headers, version="2", chat_line={"on": False, "text": "x"})
    assert settings(client)["chat_line"]["on"] is False


@pytest.mark.parametrize("text", ["", "a" * 301, "line\nbreak", "{0.__class__}"])
def test_a_bad_chat_line_is_refused(server, text):
    client, headers = server
    with_companion(client)
    got = make(client, headers, chat_line={"on": True, "text": text})
    assert got.status_code in (400, 422), got.text
    assert not (server_dir(client) / companion.MODS_FILE).exists()


def test_the_paper_plugin_also_turns_on_required_to_join(paper):
    client, headers, folder = paper
    (folder / "companion.jar").write_bytes(plugin_jar(companion.PLUGIN_NAME))
    assert options(client, headers)["required_to_join"]["available"] is True


def test_vanilla_says_it_needs_fabric(tmp_path, monkeypatch, sites):
    from .test_api import auth, token_for
    from .test_hangar_and_links import app_for

    with app_for(tmp_path, monkeypatch, "vanilla") as client:
        headers = auth(token_for(client))
        got = options(client, headers)["required_to_join"]
        refused = make(client, headers, required=True)
    assert got == {"available": False, "on": False, "needs_fabric": True}
    assert refused.status_code == 400
    assert "Fabric" in refused.json()["detail"]


# ---------------------------------------------------------------- plugin servers' players
class CompanionModrinth(ExportModrinth):
    """Modrinth with the Companion's project: the server's Paper plugin
    known by its hash, two Fabric jars, and Fabric API they need."""

    def __init__(self, plugin: bytes):
        super().__init__({"cbc": plugin})
        self.projects = {
            "cbc": {
                "project_id": "cbc",
                "title": "Command Block Companion",
                "mod_id": companion.MOD_ID,
                "versions": [("0.2.0+fabric", ["fapi"]), ("0.1.0+fabric", ["fapi"])],
            },
            "fapi": {"project_id": "fapi", "title": "Fabric API", "versions": [("0.140", [])]},
        }
        self.by_id = {"cbc": "cbc", "fapi": "fapi"}

    async def installed_versions(self, sha1s):
        found = await super().installed_versions(sha1s)
        for version in found.values():
            version["version_number"] = "0.1.0+paper"
        return found


@pytest.fixture
def paper_companion(paper, monkeypatch):
    """A Paper server with the Companion plugin from Modrinth."""
    client, headers, folder = paper
    from agent.servertypes import versions

    class Plan:
        loader = "0.16.9"

    async def plan(type_id, minecraft, loader=None):
        return Plan()

    monkeypatch.setattr(versions, "make_plan", plan)
    jar = plugin_jar(companion.PLUGIN_NAME, "0.1.0")
    (folder / "companion.jar").write_bytes(jar)
    client.app.state.core.mods.modrinth = CompanionModrinth(jar)
    return client, headers


def test_a_plugin_servers_modpack_gets_the_players_companion(paper_companion, tmp_path):
    client, headers = paper_companion
    made = make(client, headers, required=True).json()
    assert made["ok"] and made["get_yourself"] == [], made
    with zipfile.ZipFile(saved(client, headers, made, tmp_path)) as zf:
        index = json.loads(zf.read("modrinth.index.json"))
    by_path = {f["path"]: f for f in index["files"]}
    # The same version as the server's plugin, not the newest, and what it needs.
    assert sorted(by_path) == ["mods/cbc-0.1.0+fabric.jar", "mods/fapi-0.140.jar"]
    jar = by_path["mods/cbc-0.1.0+fabric.jar"]
    assert jar["env"] == {"client": "required", "server": "unsupported"}
    assert jar["downloads"] == ["https://cdn.modrinth.com/cbc.jar"]
    assert index["dependencies"]["fabric-loader"] == "0.16.9"
    # The check asks for both, with no server copy for spot checks.
    got = settings(client, companion.PLUGIN_FILE)
    assert got["required"] is True
    assert {f["path"]: f["on_server"] for f in got["files"]} == {
        "mods/cbc-0.1.0+fabric.jar": None,
        "mods/fapi-0.140.jar": None,
    }


def test_a_players_companion_put_in_by_hand_isnt_added_twice(paper_companion, relay):
    client, headers = paper_companion
    from .test_dependency_resolution import jar_bytes

    base = server_dir(client)
    (base / sides.FOLDER).mkdir()
    (base / sides.FOLDER / "mine.jar").write_bytes(jar_bytes(companion.MOD_ID, "0.1.0"))
    made = make(client, headers).json()
    assert made["ok"], made
    paths = {f["path"] for f in settings(client, companion.PLUGIN_FILE)["files"]}
    assert paths == {"mods/mine.jar", "mods/fapi-0.140.jar"}


def test_a_players_only_file_named_like_the_download_is_refused(paper_companion, relay):
    # Launchers copy the owner's file over the download, so Fabric API would be gone.
    client, headers = paper_companion
    from .test_dependency_resolution import jar_bytes

    base = server_dir(client)
    (base / sides.FOLDER).mkdir()
    (base / sides.FOLDER / "fapi-0.140.jar").write_bytes(jar_bytes("other_mod", "1.0"))
    refused = make(client, headers)
    assert refused.status_code == 400
    assert "Two files are named fapi-0.140.jar" in refused.json()["detail"]


def test_a_dependency_gone_from_modrinth_says_get_it_yourself(paper_companion, monkeypatch):
    client, headers = paper_companion
    from agent.mods.modrinth import ModrinthNotFound

    async def gone(*args):
        raise ModrinthNotFound("404")

    monkeypatch.setattr(client.app.state.core.mods.modrinth, "latest_for", gone)
    made = make(client, headers).json()
    assert made["ok"], made
    assert made["get_yourself"] == [
        {"name": "Command Block Companion for Fabric", "page": "https://modrinth.com/mod/cbc"}
    ]


def test_a_plugin_not_from_modrinth_says_get_the_players_companion(paper_companion):
    client, headers = paper_companion
    client.app.state.core.mods.modrinth = ExportModrinth({})
    made = make(client, headers).json()
    assert made["ok"], made
    assert made["get_yourself"] == [{"name": "Command Block Companion for Fabric", "page": None}]
    assert settings(client, companion.PLUGIN_FILE)["files"] == []


@pytest.mark.parametrize("server_type", ["purpur", "leaf"])
def test_purpur_and_leaf_run_the_paper_plugin(tmp_path, monkeypatch, sites, server_type):
    from .test_api import auth, token_for
    from .test_hangar_and_links import app_for

    with app_for(tmp_path, monkeypatch, server_type) as client:
        (client.app.state.core.mods.mods_dir / "companion.jar").write_bytes(
            plugin_jar(companion.PLUGIN_NAME)
        )
        got = options(client, auth(token_for(client)))
    assert got["required_to_join"]["available"] is True


def test_quilt_runs_the_fabric_companion(tmp_path, monkeypatch, sites):
    from .test_api import auth, token_for
    from .test_hangar_and_links import app_for

    with app_for(tmp_path, monkeypatch, "quilt") as client:
        with_companion(client)
        got = options(client, auth(token_for(client)))
    assert got["required_to_join"]["available"] is True
