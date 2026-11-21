# ruff: noqa: F811  (the fixtures imported from other tests are used by name)
"""Make a modpack from a server and share it.

The made .mrpack is read back with the same reader "New server from a
modpack" uses, so what one owner makes another can import: mods by hash
with their Server / Players / Both marks, own jars in the right override
folder, the ticked config files, and a plugin server's command-block.json.
CurseForge mods whose authors keep them from other apps never go in.
"""

from __future__ import annotations

import hashlib
import json
import zipfile

import httpx
import pytest

from agent import modpack, modpackmake
from agent.discord import client as discord_client
from agent.mods import sides
from agent.security import permissions as P

from .test_curseforge import MC, FakeCurseForge, fabric, fabric_jar, relay  # noqa: F401
from .test_dependency_resolution import FakeModrinth
from .test_hangar_and_links import paper, plugin_jar, sites, zip_of  # noqa: F401
from .test_own_jars import moderator

SODIUM = fabric_jar("sodium", "0.6")
OWN = fabric_jar("ourtweaks", "1.0")
SERVERONLY = fabric_jar("ledger", "1.3")
PACK = zip_of({"pack.mcmeta": '{"pack": {"pack_format": 46}}', "assets/x.png": b"png"})


class ExportModrinth(FakeModrinth):
    """Modrinth knowing some files by hash (``/version_files``)."""

    def __init__(self, files: dict[str, bytes], fail: bool = False):
        super().__init__({}, mc=MC)
        self.files, self.fail, self.asked = files, fail, 0

    async def installed_versions(self, sha1s):
        from agent.mods.modrinth import ModrinthError

        self.asked += 1
        if self.fail:
            raise ModrinthError("down")
        found = {}
        for name, data in self.files.items():
            sha1 = hashlib.sha1(data).hexdigest()
            if sha1 in sha1s:
                found[sha1] = {
                    "project_id": name,
                    "file": {
                        "filename": f"{name}.jar",
                        "url": f"https://cdn.modrinth.com/data/{name}/versions/v1/{name}.jar",
                        "sha1": sha1,
                        "sha512": hashlib.sha512(data).hexdigest(),
                    },
                }
        return found


def server_dir(client):
    return client.app.state.core.mods.config.server_dir


def api(client, rest):
    server_id = next(iter(client.app.state.core.servers))
    return f"/api/servers/{server_id}/{rest}"


def make(client, headers, **body):
    body = {"name": "Friends", "version": "1", **body}
    return client.post(api(client, "modpack/make"), json=body, headers=headers)


def saved(client, headers, made, tmp_path):
    got = client.get(api(client, f"modpack/made/{made['token']}"), headers=headers)
    assert got.status_code == 200, got.text
    assert "Friends%201.mrpack" in got.headers["content-disposition"]
    path = tmp_path / "made.mrpack"
    path.write_bytes(got.content)
    return path


@pytest.fixture
def server(fabric, relay):
    """A Fabric server with a Modrinth mod, a CurseForge mod that may be
    shared, one that may not, an own jar, a server-only own jar, a
    resource pack for players and a config file."""
    client, headers, folder = fabric
    core = client.app.state.core
    core.mods.modrinth = ExportModrinth({"sodium": SODIUM})
    (folder / "sodium.jar").write_bytes(SODIUM)
    (folder / "lithium.jar").write_bytes(relay.jar(5001))
    (folder / "secret.jar").write_bytes(relay.jar(3301))
    (folder / "ourtweaks.jar").write_bytes(OWN)
    (folder / "ledger.jar").write_bytes(SERVERONLY)
    base = server_dir(client)
    (base / sides.MARKS_FILE).write_text(json.dumps({"ledger": "server"}))
    (base / sides.FOLDER).mkdir()
    (base / sides.FOLDER / "pretty.zip").write_bytes(PACK)
    (base / "config" / "sodium.json").write_text('{"fast": true}')
    (base / "config" / "private.json").write_text('{"password": "x"}')
    return client, headers


# ---------------------------------------------------------------- the round trip
def test_a_made_modpack_reads_back_as_the_same_server(server, tmp_path):
    client, headers = server
    options = client.get(api(client, "modpack/make"), headers=headers).json()
    assert options["name"] and options["version"] == "1"
    assert {"path": "config/sodium.json", "size_bytes": 14} in options["configs"]
    assert options["players_loaders"] == [], "a Fabric server uses its own loader"
    assert options["required_to_join"] == {"available": False, "on": False}, "no Companion here"

    made = make(client, headers, leave_out_server_only=False, configs=["config/sodium.json"]).json()
    assert made["ok"], made
    pack = modpack.inspect(saved(client, headers, made, tmp_path))
    assert pack.minecraft == MC and pack.type_id == "fabric" and pack.loader_version == "0.16.5"
    by_path = {f.path: f for f in pack.files}
    assert by_path["mods/sodium.jar"].sha512 == hashlib.sha512(SODIUM).hexdigest()
    assert by_path["mods/sodium.jar"].url.startswith("https://cdn.modrinth.com/")
    lithium = by_path["mods/lithium.jar"]
    assert lithium.status == "download" and "forgecdn.net" in lithium.url, "CurseForge allows it"
    assert (by_path["mods/sodium.jar"].server, by_path["mods/sodium.jar"].client) == (
        "required",
        "required",
    )
    # Never put in: its author keeps it from other apps.
    assert "mods/secret.jar" not in by_path
    assert [n["name"] for n in made["get_yourself"]] == ["Secret Mod"]
    # Own files, each in its side's folder, and listed as not checked by a site.
    assert sorted(made["own_files"]) == [
        "client-overrides/resourcepacks/pretty.zip",
        "overrides/mods/ourtweaks.jar",
        "server-overrides/mods/ledger.jar",
    ]
    assert sorted(pack.own_jars) == ["mods/ledger.jar", "mods/ourtweaks.jar"]
    assert made["trust_note"] and "trust" in pack.summary and "Secret Mod" in pack.summary
    with zipfile.ZipFile(tmp_path / "made.mrpack") as zf:
        names = set(zf.namelist())
        assert zf.read("overrides/config/sodium.json") == b'{"fast": true}'
        assert zf.getinfo("overrides/mods/ourtweaks.jar").compress_type == zipfile.ZIP_STORED
    assert "overrides/config/private.json" not in names, "only the ticked config files"
    assert not pack.problems


def test_leave_out_server_only_mods_drops_them_and_server_overrides(server, tmp_path):
    client, headers = server
    made = make(client, headers).json()  # ticked by default
    assert made["left_out"] == ["Ledger"]
    with zipfile.ZipFile(saved(client, headers, made, tmp_path)) as zf:
        names = zf.namelist()
        index = json.loads(zf.read("modrinth.index.json"))
    assert not [n for n in names if n.startswith("server-overrides/")]
    assert all(f["env"]["client"] != "unsupported" for f in index["files"])


def test_a_mod_marked_players_is_a_client_only_entry(server, tmp_path):
    client, headers = server
    base = server_dir(client)
    (base / sides.MARKS_FILE).write_text(json.dumps({"sodium": "players", "ledger": "server"}))
    made = make(client, headers).json()
    with zipfile.ZipFile(saved(client, headers, made, tmp_path)) as zf:
        index = json.loads(zf.read("modrinth.index.json"))
    sodium = next(f for f in index["files"] if f["path"] == "mods/sodium.jar")
    assert sodium["env"] == {"client": "required", "server": "unsupported"}


def test_only_the_newest_modpack_is_kept_and_served(server):
    client, headers = server
    first = make(client, headers).json()
    second = make(client, headers, version="2").json()
    assert (
        client.get(api(client, f"modpack/made/{first['token']}"), headers=headers).status_code
        == 404
    )
    assert (
        client.get(api(client, f"modpack/made/{second['token']}"), headers=headers).status_code
        == 200
    )
    options = client.get(api(client, "modpack/make"), headers=headers).json()
    assert options["version"] == "3" and options["made"]["token"] == second["token"]
    folder = client.app.state.core.config.data_dir / modpackmake.FOLDER
    assert len(list(folder.rglob("*.mrpack*"))) == 1


# ---------------------------------------------------------------- what's refused
@pytest.mark.parametrize(
    "config", ["server.properties", "config/../server.properties", "world/level.dat"]
)
def test_only_offered_config_files_can_go_in(server, config):
    client, headers = server
    refused = make(client, headers, configs=[config])
    assert refused.status_code == 400
    assert "config files" in refused.json()["detail"]


@pytest.mark.parametrize("name", ["", "a/b", "x" * 65, " spaced"])
def test_a_name_that_cant_be_a_file_name_is_refused(server, name):
    client, headers = server
    assert make(client, headers, name=name).status_code in (400, 422)


@pytest.mark.parametrize("version", ["\u00e92", "\u0663", "1 beta", "1\n"])
def test_a_version_players_companions_cant_read_is_refused(server, version):
    # Python's \w lets in other languages' letters, but the Companion only reads A-Z, 0-9 and _ . + -
    client, headers = server
    assert make(client, headers, version=version).status_code in (400, 422)


def test_modrinth_down_makes_nothing(server):
    client, headers = server
    client.app.state.core.mods.modrinth.fail = True
    refused = make(client, headers)
    assert refused.status_code == 400 and "Modrinth didn't answer" in refused.json()["detail"]
    assert client.get(api(client, "modpack/make"), headers=headers).json()["made"] is None


def test_curseforge_down_makes_nothing_rather_than_guess(server, relay):
    client, headers = server
    relay.state = "down"
    refused = make(client, headers)
    assert refused.status_code == 400 and "CurseForge didn't answer" in refused.json()["detail"]


def test_the_version_is_never_guessed(server):
    client, headers = server
    client.app.state.core.mods.server.mc_version = None
    refused = make(client, headers)
    assert refused.status_code == 400 and "Start" in refused.json()["detail"]


def test_making_one_needs_the_add_ons_permission(server):
    client, owner = server
    sam = moderator(client, owner, {P.SERVER_VIEW})
    assert make(client, sam).status_code == 403
    assert client.get(api(client, "modpack/make"), headers=sam).status_code == 403


# ---------------------------------------------------------------- plugin servers
def test_a_paper_server_makes_a_modpack_another_owner_reads_as_paper(paper, tmp_path, monkeypatch):
    client, headers, folder = paper
    core = client.app.state.core
    essentials = plugin_jar("Essentials", "2.20")
    core.mods.modrinth = ExportModrinth({"essentials": essentials, "sodium": SODIUM})
    (folder / "Essentials.jar").write_bytes(essentials)
    (folder / "Trees.jar").write_bytes(plugin_jar("Trees"))
    base = server_dir(client)
    (base / sides.FOLDER).mkdir()
    (base / sides.FOLDER / "sodium.jar").write_bytes(SODIUM)

    from agent.servertypes import versions

    class Plan:
        loader = "0.16.9"

    async def plan(type_id, minecraft, loader=None):
        assert (type_id, minecraft) == ("fabric", MC)
        return Plan()

    monkeypatch.setattr(versions, "make_plan", plan)
    options = client.get(api(client, "modpack/make"), headers=headers).json()
    assert [x["id"] for x in options["players_loaders"]] == ["fabric", "quilt"]

    made = make(client, headers, leave_out_server_only=False).json()
    assert made["plugins_to_get"] == ["Trees"]
    path = saved(client, headers, made, tmp_path)
    pack = modpack.inspect(path)
    assert pack.type_id == "paper" and pack.plugins_to_get == ["Trees"]
    by_path = {f.path: f for f in pack.files}
    assert by_path["plugins/Essentials.jar"].server == "required"
    assert by_path["plugins/Essentials.jar"].client == "unsupported"
    assert by_path["mods/sodium.jar"].server == "unsupported"
    with zipfile.ZipFile(path) as zf:
        index = json.loads(zf.read("modrinth.index.json"))
        assert "plugins/Trees.jar" not in str(zf.namelist()), "listed, never put in"
    assert index["dependencies"] == {"minecraft": MC, "fabric-loader": "0.16.9"}

    # For friends: only the players' side, no command-block.json.
    friends = make(client, headers).json()
    with zipfile.ZipFile(saved(client, headers, friends, tmp_path)) as zf:
        assert not [n for n in zf.namelist() if n.startswith("server-overrides/")]
        index = json.loads(zf.read("modrinth.index.json"))
    assert [f["path"] for f in index["files"]] == ["mods/sodium.jar"]


# ---------------------------------------------------------------- the Discord bot
class FakeBot:
    connected = True
    guilds = {"g1": {"id": "g1"}}

    def __init__(self, server_id):
        self.server_id = server_id
        self.posts = []
        bot = self

        class Rest:
            async def request(self, method, path, body=None, files=None):
                bot.posts.append((method, path, body, files))

        self.rest = Rest()

    async def stop(self):
        pass

    def chat_channels(self, guild_ids):
        return [
            {"kind": "chat", "server_id": self.server_id, "channel_id": "c1"},
            {"kind": "chat", "server_id": "other", "channel_id": "c2"},
        ]


def test_the_bot_posts_the_modpack_in_the_servers_chat_channel(server):
    client, headers = server
    core = client.app.state.core
    ctx = next(iter(core.servers.values()))
    core.discord = bot = FakeBot(ctx.server_id)
    made = make(client, headers).json()
    assert made["discord"] is True
    posted = client.post(
        api(client, "modpack/made/discord"), json={"token": made["token"]}, headers=headers
    )
    assert posted.status_code == 200, posted.text
    assert posted.json()["posted"] == 1
    method, path, body, files = bot.posts[0]
    assert path == "/channels/c1/messages" and body["allowed_mentions"] == {"parse": []}
    assert "trust" in body["content"] and "Secret Mod" in body["content"]
    name, data, _ = files["files[0]"]
    assert name == "Friends 1.mrpack" and data.startswith(b"PK")


def test_a_modpack_too_big_for_discord_isnt_posted(server, monkeypatch):
    client, headers = server
    core = client.app.state.core
    ctx = next(iter(core.servers.values()))
    core.discord = bot = FakeBot(ctx.server_id)
    monkeypatch.setattr(modpackmake, "DISCORD_LIMIT", 10)
    made = make(client, headers).json()
    assert made["discord"] is False
    refused = client.post(
        api(client, "modpack/made/discord"), json={"token": made["token"]}, headers=headers
    )
    assert refused.status_code == 400 and "10 MB" in refused.json()["detail"]
    assert not bot.posts


async def test_discord_gets_the_file_beside_the_message(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["type"] = request.headers["content-type"]
        seen["body"] = request.content
        return httpx.Response(200, json={"id": "m1"})

    monkeypatch.setattr(discord_client, "TRANSPORT", httpx.MockTransport(handler))
    rest = discord_client.Rest("token")
    try:
        await rest.request(
            "POST",
            "/channels/c1/messages",
            {"content": "hi"},
            files={"files[0]": ("p.mrpack", b"PK data", "application/zip")},
        )
    finally:
        await rest.close()
    assert seen["type"].startswith("multipart/form-data")
    assert b'name="payload_json"' in seen["body"] and b'{"content": "hi"}' in seen["body"]
    assert b'filename="p.mrpack"' in seen["body"] and b"PK data" in seen["body"]
