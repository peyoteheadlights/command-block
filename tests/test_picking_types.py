"""Picking a server type: hints and a goal, Leaf and Purpur in Technical,
the owner's own server jar, vanilla spawn settings, and types the notes
file adds. Every official source is the fake network from
test_server_types.py, plus a fake Leaf API."""

from __future__ import annotations

import hashlib
import json
import zipfile

import httpx
import pytest
import yaml

from agent import downloads, gamesettings, notes, servertypes, signing
from agent.config import Config
from agent.core import AgentCore
from agent.minecraft.console import ConsoleLine, extract_signals
from agent.servertypes import create as create_module
from agent.servertypes import custom
from agent.servertypes import versions as versions_module
from agent.softwarecheck import build_of

from .conftest import build_multi_config
from .test_keeping_current import NOW, notes_body, signed
from .test_server_types import MC, SERVER_JAR, fake_sources

LEAF_SHA = hashlib.sha256(SERVER_JAR).hexdigest()


def leaf_and_the_rest(leaf_sha: str = LEAF_SHA):
    base = fake_sources()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "api.leafmc.one":
            return base(request)
        path = request.url.path
        if path.endswith("/downloads/leaf-1.21.1-12.jar"):
            return httpx.Response(200, content=SERVER_JAR)
        if path.endswith("/builds"):
            # oldest first, as PaperMC's v2 API was; the newest is experimental
            return httpx.Response(
                200,
                json={
                    "builds": [
                        {
                            "build": 11,
                            "channel": "default",
                            "downloads": {"primary": {"name": "leaf-1.21.1-11.jar"}},
                        },
                        {
                            "build": 12,
                            "channel": "default",
                            "downloads": {
                                "primary": {"name": "leaf-1.21.1-12.jar", "sha256": leaf_sha}
                            },
                        },
                        {
                            "build": 13,
                            "channel": "experimental",
                            "downloads": {"primary": {"name": "leaf-1.21.1-13.jar"}},
                        },
                    ]
                },
            )
        return httpx.Response(200, json={"project_id": "leaf", "versions": ["1.20.6", MC]})

    return handler


@pytest.fixture
def network(monkeypatch):
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(leaf_and_the_rest()))
    monkeypatch.setattr(create_module, "java_problem", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def built_in_types_only(monkeypatch):
    """Types a test adds (from the notes file) go away after it."""
    monkeypatch.setattr(servertypes, "TYPES", dict(servertypes.TYPES))


@pytest.fixture
async def core(tmp_path, network):
    agent = AgentCore(build_multi_config(tmp_path))
    yield agent
    await agent.jobs.stop()
    agent.db.close()


async def new_server(core, tmp_path, type_id: str, name: str = "Lobby", **extra) -> dict:
    return await create_module.create_server(
        core,
        name=name,
        directory=str(tmp_path / name),
        type_id=type_id,
        minecraft=MC,
        eula_accepted=True,
        **extra,
    )


def a_jar(path, content: bytes = b"a pretend fork") -> str:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/MANIFEST.MF", "Main-Class: Fork\n")
        zf.writestr("fork.txt", content)
    return str(path)


# ---------------------------------------------------------------- hints and a goal
def test_plain_vanilla_gets_vanilla_and_fabric_and_warns_about_paper():
    advice = servertypes.recommend("vanilla")
    assert advice["types"] == ["vanilla", "fabric"]
    assert advice["recommended"] == ["vanilla", "fabric"]
    assert {"paper", "purpur", "leaf"} <= set(advice["warn"])


def test_plugins_get_paper_and_technical_adds_the_forks():
    assert servertypes.recommend("plugins") == {
        "goal": "plugins",
        "types": ["paper"],
        "recommended": ["paper"],
        "warn": [],
    }
    technical = servertypes.recommend("plugins", technical=True)
    assert technical["types"] == ["paper", "purpur", "leaf"]
    assert technical["recommended"] == ["paper"], "a fork is never the recommended pick"


@pytest.mark.parametrize(
    ("loaders", "fits", "pick"),
    [
        ((), ["fabric", "quilt", "forge", "neoforge"], ["fabric"]),
        (("fabric",), ["fabric", "quilt"], ["fabric"]),
        (("neoforge",), ["neoforge"], ["neoforge"]),
        (("forge", "neoforge"), ["forge", "neoforge"], ["forge"]),
    ],
)
def test_mods_get_only_the_loaders_the_picked_mods_support(loaders, fits, pick):
    advice = servertypes.recommend("mods", loaders)
    assert (advice["types"], advice["recommended"]) == (fits, pick)


def test_the_phone_apps_get_the_same_recommendation(multi_client):
    answer = multi_client.get("/api/server-types/recommend?goal=mods&loaders=neoforge").json()
    assert answer["types"] == ["neoforge"]
    assert multi_client.get("/api/server-types/recommend?goal=minigames").status_code == 400


def test_every_type_says_what_it_suits_and_which_are_technical_only():
    catalog = {t["id"]: t for t in servertypes.catalog()}
    assert catalog["leaf"]["technical_only"] and catalog["leaf"]["never_recommended"]
    assert catalog["purpur"]["technical_only"] and not catalog["purpur"]["never_recommended"]
    assert not catalog["paper"]["technical_only"]
    assert catalog["paper"]["changes_farms"] and not catalog["fabric"]["changes_farms"]
    assert all(catalog[t]["goals"] for t in catalog if catalog[t]["edition"] == "java")


# ---------------------------------------------------------------- Leaf and Purpur
async def test_leaf_versions_and_builds_come_from_leafs_api(network):
    found = await versions_module.list_versions("leaf")
    assert [v.minecraft for v in found] == [MC, "1.20.6"]
    plan = await versions_module.make_plan("leaf", MC)
    # the newest build marked finished, not the experimental one after it
    assert (plan.loader, plan.stable, plan.downloads[0].spec.sha256) == ("12", True, LEAF_SHA)
    assert plan.downloads[0].spec.url.endswith("/builds/12/downloads/leaf-1.21.1-12.jar")


async def test_a_leaf_server_is_set_up_and_its_file_checked(core, tmp_path):
    result = await new_server(core, tmp_path, "leaf")
    assert result["verified"] is True
    assert (tmp_path / "Lobby" / "leaf.jar").read_bytes() == SERVER_JAR
    assert core.get_server(result["server_id"]).config.mods_dir.name == "plugins"


async def test_a_leaf_download_that_doesnt_match_its_checksum_is_refused(
    core, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        downloads, "TRANSPORT", httpx.MockTransport(leaf_and_the_rest(leaf_sha="0" * 64))
    )
    with pytest.raises(Exception, match="(?i)checksum|sha"):
        await new_server(core, tmp_path, "leaf")
    assert not (tmp_path / "Lobby").exists()


def test_leafs_console_line_names_leaf_and_a_dev_build_is_no_build_number():
    line = ConsoleLine(
        seq=0,
        ts=0,
        raw="",
        message="This server is running Leaf version 1.21.1-DEV-ver/1.21.1@abc (MC: 1.21.1)",
    )
    signals = extract_signals(line)
    assert (signals.loader_name, signals.mc_version) == ("Leaf", "1.21.1")
    assert servertypes.detected_type(signals.loader_name) == "leaf"
    assert build_of("leaf", "1.21.1-DEV-ver/1.21.1@abc") is None
    assert build_of("leaf", "1.21.1-12-ver/1.21.1@abc") == "12"


def test_a_chat_line_cant_pretend_to_be_the_loader_line():
    line = ConsoleLine(
        seq=0, ts=0, raw="", message="<Bob> This server is running Fake version 1 (MC: 9.9)"
    )
    assert extract_signals(line).loader_name is None


async def test_an_existing_purpur_server_still_lists_and_changes_versions(network):
    assert servertypes.get("purpur").technical_only
    assert [v.minecraft for v in await versions_module.list_versions("purpur")]


# ---------------------------------------------------------------- vanilla spawns
async def test_a_new_paper_server_can_start_with_vanilla_spawn_settings(core, tmp_path):
    await new_server(core, tmp_path, "paper", vanilla_spawns=True)
    data = yaml.safe_load((tmp_path / "Lobby" / "spigot.yml").read_text())
    ranges = data["world-settings"]["default"]["entity-activation-range"]
    assert ranges["monsters"] == ranges["villagers"] == 0
    assert data["world-settings"]["default"]["nerf-spawner-mobs"] is False


async def test_vanilla_spawn_settings_keep_the_rest_of_the_file_and_undo_puts_it_back(
    core, tmp_path
):
    result = await new_server(core, tmp_path, "paper")
    ctx = core.get_server(result["server_id"])
    spigot = tmp_path / "Lobby" / "spigot.yml"
    before = "settings:\n  debug: false\nworld-settings:\n  default:\n    mob-spawn-range: 6\n"
    spigot.write_text(before)
    saved = await gamesettings.use_vanilla_spawns(ctx, user="admin")
    assert saved["undo"]
    data = yaml.safe_load(spigot.read_text())
    assert data["settings"]["debug"] is False
    assert data["world-settings"]["default"]["mob-spawn-range"] == 6
    assert gamesettings.read_vanilla_spawns(tmp_path / "Lobby")
    await ctx.backups.changes.undo("admin")
    assert spigot.read_text() == before


async def test_vanilla_spawn_settings_are_only_for_types_that_change_farms(core):
    with pytest.raises(Exception, match="already spawns mobs like vanilla"):
        await gamesettings.use_vanilla_spawns(core.servers["survival"])


# ---------------------------------------------------------------- the owner's own jar
def custom_request(tmp_path, **extra) -> dict:
    return {
        "name": "My fork",
        "directory": str(tmp_path / "fork"),
        "jar": a_jar(tmp_path / "myfork-1.0.jar"),
        "based_on": "paper",
        "eula_accepted": True,
        "trusted": True,
        **extra,
    }


def test_the_owner_runs_their_own_jar_based_on_a_type(multi_client, multi, tmp_path):
    answer = multi_client.post("/api/new-server/custom-jar", json=custom_request(tmp_path))
    assert answer.status_code == 200, answer.text
    assert answer.json()["verified"] is False
    assert answer.json()["server"]["custom_jar"] is True
    settings = multi.for_server("my-fork")
    assert (settings.server.type, settings.server.jar) == ("paper", "myfork-1.0.jar")
    assert settings.server.custom_jar is True
    assert settings.mods_dir.name == "plugins", "add-ons work like Paper's"
    assert (tmp_path / "fork" / "myfork-1.0.jar").is_file()
    assert "eula=true" in (tmp_path / "fork" / "eula.txt").read_text()
    version = multi_client.get("/api/servers/my-fork/version").json()
    assert version["custom_jar"] is True and version["minecraft_version"] is None


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"trusted": False}, "trust"),
        ({"eula_accepted": False}, "EULA"),
        ({"based_on": "bedrock"}, "Java server type"),
    ],
)
def test_a_custom_jar_needs_the_owners_ticks_and_a_java_type(multi_client, tmp_path, change, said):
    answer = multi_client.post(
        "/api/new-server/custom-jar", json=custom_request(tmp_path, **change)
    )
    assert answer.status_code == 400 and said in answer.json()["detail"]
    assert not (tmp_path / "fork").exists()


def test_a_file_that_isnt_a_jar_is_refused(multi_client, tmp_path):
    fake = tmp_path / "virus.jar"
    fake.write_bytes(b"MZ not a zip")
    answer = multi_client.post(
        "/api/new-server/custom-jar", json=custom_request(tmp_path, jar=str(fake))
    )
    assert answer.status_code == 400 and "can be opened" in answer.json()["detail"]


def test_a_jar_on_the_bad_file_list_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "bad.jar"
    a_jar(path)
    monkeypatch.setitem(notes.BAD_FILES, notes.sha512_of(path), {"why": "It steals accounts"})
    with pytest.raises(custom.InstallError, match="steals accounts"):
        custom.check_jar(str(path))


def test_a_custom_jar_is_never_version_changed(multi_client, tmp_path):
    multi_client.post("/api/new-server/custom-jar", json=custom_request(tmp_path))
    answer = multi_client.post(
        "/api/servers/my-fork/version/preflight", json={"minecraft_version": MC}
    )
    assert answer.status_code == 400 and "your own server jar" in answer.json()["detail"]


async def test_a_custom_jar_is_never_offered_an_update(core, tmp_path):
    await custom.create_custom(core, **{**custom_request(tmp_path), "user": "admin"})
    ctx = core.get_server("my-fork")
    ctx.server.mc_version = MC
    assert await core.software.check_server(ctx) == {}


async def test_replacing_the_jar_backs_it_up_and_one_undo_puts_it_back(core, tmp_path):
    await custom.create_custom(core, **{**custom_request(tmp_path), "user": "admin"})
    ctx = core.get_server("my-fork")
    old = (tmp_path / "fork" / "myfork-1.0.jar").read_bytes()
    result = await custom.replace_jar(ctx, a_jar(tmp_path / "myfork-1.1.jar", b"newer"))
    assert result["undo"] and result["verified"] is False
    jar = tmp_path / "fork" / "myfork-1.0.jar"
    assert jar.read_bytes() != old
    await ctx.backups.changes.undo("admin")
    assert jar.read_bytes() == old


# ---------------------------------------------------------------- types from the notes file
FOLIA = {
    "id": "folia",
    "name": "Folia",
    "like": "paper",
    "hint": "Paper split into regions for very big servers.",
    "goals": ["plugins"],
}


def test_a_signed_notes_file_adds_a_type_that_reuses_paper(monkeypatch):
    private, public = signing.new_key()
    document, signature = signed(notes_body(server_types=[FOLIA]), private)
    found = notes.check(document, signature, [public], 0, NOW, "0.9.0")
    holder = notes.SafetyNotes.__new__(notes.SafetyNotes)
    holder._apply(found)
    folia = {t["id"]: t for t in servertypes.catalog()}["folia"]
    assert folia["technical_only"] and folia["never_recommended"] and folia["like"] == "paper"
    assert folia["hint"] == FOLIA["hint"] and folia["version_host"] == "fill.papermc.io"
    assert "folia" in servertypes.recommend("plugins", technical=True)["types"]
    assert servertypes.recommend("plugins", technical=True)["recommended"] == ["paper"]


@pytest.mark.parametrize(
    "change",
    [{"like": "fabric"}, {"like": "custom"}, {"id": "paper"}, {"id": "Folia!"}, {"goals": ["x"]}],
)
def test_a_notes_type_can_only_reuse_what_the_code_already_has(change):
    with pytest.raises(notes.NotesRefused):
        notes.parse(
            json.dumps(notes_body(server_types=[{**FOLIA, **change}])).encode(), 0, NOW, "0.9.0"
        )


async def test_a_server_of_a_notes_type_downloads_from_fill_and_loads_without_the_notes(
    core, tmp_path, monkeypatch
):
    servertypes.register_variant("folia", "paper", "Folia")
    result = await new_server(core, tmp_path, "folia")
    assert (tmp_path / "Lobby" / "folia.jar").read_bytes() == SERVER_JAR
    entry = core.config.server_entry(result["server_id"])
    assert (entry["type"], entry["like"]) == ("folia", "paper")
    # The next start, before the notes file is read: the config names its base.
    del servertypes.TYPES["folia"]
    again = Config.load(core.config.source)
    assert again.for_server(result["server_id"]).server_type.like == "paper"
