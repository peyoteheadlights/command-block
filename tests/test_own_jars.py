# ruff: noqa: F811  (the fixtures imported from other tests are used by name)
""" "Add own .jar" and reading other owners' modpacks.

An own jar is one Modrinth, Hangar and CurseForge don't list: nothing can
tell what it does, and it runs with the server's rights on the PC. Adding
one by hand, or from a modpack, needs the "Add own .jar" permission
(owner only until the owner ticks it for a moderator). A modpack's own
jars are listed as "not checked by a site" before the import, and the
import only takes them when the person said yes to that list.

A modpack from another Command Block owner can also carry
``client-overrides/`` (kept in the player-only folder, never loaded by the
server) and ``server-overrides/command-block.json`` (the plugin server it
was made from, since a .mrpack can only name Fabric, Quilt, Forge or
NeoForge).
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from agent import linkedpack, modpack
from agent.modpack import ModpackError
from agent.security import permissions as P

from .test_hangar_and_links import (  # noqa: F401
    PluginModrinth,
    paper,
    plugin_jar,
    sites,
    upload,
    zip_of,
)
from .test_helpers import HELPER_PASSWORD, add_helper, sign_in
from .test_linked_modpack import FakeModrinth
from .test_modpack import (  # noqa: F401
    core,
    make_pack,
    mod_entry,
    network,
    requests_made,
)
from .test_modpack import upload as upload_pack

OWN = b"PK\x03\x04 a jar nobody lists" * 40


# ---------------------------------------------------------------- the permission
def test_add_own_jar_is_a_switch_no_level_has():
    assert P.MODS_OWN_JAR in P.SERVER_PERMISSIONS
    assert P.MODS_OWN_JAR in dict(P.GROUPS)["addons"]
    for level in P.LEVELS.values():
        assert P.MODS_OWN_JAR not in level
    # It means nothing without adding mods at all.
    assert P.grant({P.MODS_OWN_JAR}) >= {P.MODS_MANAGE}
    assert P.MODS_OWN_JAR in P.TECHNICAL


def test_every_switch_has_words_in_the_apps():
    words = json.loads(open("shared/strings.json", encoding="utf-8").read())
    for _group, perms in P.GROUPS:
        for permission in perms:
            assert f"mobile.mod.switch_{permission.replace('.', '_')}" in words


# ---------------------------------------------------------------- by hand
def moderator(client, owner, grants):
    add_helper(client, owner)
    body = {"grants": {"test": sorted(grants)}}
    response = client.put("/api/accounts/sam", json=body, headers=owner)
    assert response.status_code == 200, response.text
    return sign_in(client, "sam", HELPER_PASSWORD)


def test_a_moderator_without_the_switch_cant_add_a_jar_no_site_lists(paper):
    client, owner, folder = paper
    sam = moderator(client, owner, P.RUN_IT)
    refused = upload(client, sam, "Trees-1.0.jar", plugin_jar("Trees"))
    assert refused.status_code == 403
    assert "Add own .jar" in refused.json()["detail"]
    assert not list(folder.glob("*.jar")) and not list(folder.glob(".*.part"))
    # Not even the "can't tell what it is" question: it's refused first.
    asked = upload(client, sam, "Trees-1.0.jar", plugin_jar("Trees"), confirm=False)
    assert asked.status_code == 403


def test_a_moderator_without_the_switch_can_still_add_what_a_site_lists(paper):
    client, owner, folder = paper
    sam = moderator(client, owner, P.RUN_IT)
    essentials = plugin_jar("essentials", "2.20")
    import hashlib

    client.app.state.core.mods.modrinth = PluginModrinth({hashlib.sha512(essentials).hexdigest()})
    added = upload(client, sam, "ess.jar", essentials)
    assert added.status_code == 200, added.text
    assert added.json()["source"] == "modrinth"


def test_a_moderator_with_the_switch_adds_own_jars(paper):
    client, owner, folder = paper
    sam = moderator(client, owner, P.RUN_IT | {P.MODS_OWN_JAR})
    added = upload(client, sam, "Trees-1.0.jar", plugin_jar("Trees"))
    assert added.status_code == 200, added.text
    assert (folder / "Trees-1.0.jar").is_file()


def test_a_github_jar_needs_the_switch_too(paper, sites):
    client, owner, folder = paper
    sam = moderator(client, owner, P.RUN_IT)
    read = client.post(
        "/api/mods/link",
        json={"url": "https://github.com/tree-maker/trees/releases/tag/v1.2"},
        headers=sam,
    ).json()
    refused = client.post(
        "/api/mods/link/add",
        json={"version_id": read["files"][0]["version_id"], "confirm": True},
        headers=sam,
    )
    assert refused.status_code == 403
    assert not list(folder.glob("*.jar"))


def test_a_players_only_jar_needs_the_switch_but_a_resource_pack_doesnt(paper):
    client, owner, folder = paper
    sam = moderator(client, owner, P.RUN_IT)
    server = client.app.state.core.config.server_dir
    fabric = zip_of({"fabric.mod.json": json.dumps({"id": "minimap", "version": "1"})})
    refused = client.post(
        "/api/mods/player-only/upload?confirm=true",
        files={"file": ("minimap.jar", io.BytesIO(fabric))},
        headers=sam,
    )
    assert refused.status_code == 403
    assert not (server / "player-only" / "minimap.jar").exists()
    added = client.post(
        "/api/mods/player-only/upload?confirm=true",
        files={"file": ("faithful.zip", io.BytesIO(zip_of({"pack.mcmeta": "{}"})))},
        headers=sam,
    )
    assert added.status_code == 200, added.text


# ---------------------------------------------------------------- a modpack's own jars
def own_jar_pack(path, **extra):
    return make_pack(
        path,
        overrides={
            "overrides/config/lithium.properties": "mixin.ai=false\n",
            "overrides/mods/our-tweaks.jar": OWN,
            **extra,
        },
    )


def test_a_packs_own_jars_are_listed_as_not_checked_by_a_site(tmp_path):
    view = modpack.inspect(own_jar_pack(tmp_path / "p.mrpack")).to_dict()
    assert view["own_jars"] == ["mods/our-tweaks.jar"]
    assert view["can_import"] is True


@pytest.mark.parametrize(
    "member",
    [
        "overrides/plugins/Evil.JAR",  # any .jar, any case, anywhere
        "overrides/libraries/net/x/win_args.txt",  # the server's own program files
        "server-overrides/fabric-server-launch.jar",
        "server-overrides/server.jar",
        "overrides/Libraries/x.txt",  # Windows doesn't mind the case
    ],
)
def test_anything_that_runs_as_the_server_counts_as_an_own_jar(tmp_path, member):
    pack = modpack.inspect(make_pack(tmp_path / "p.mrpack", overrides={member: "x"}))
    assert pack.own_jars == [member.split("/", 1)[1]]


async def test_a_pack_with_own_jars_is_only_imported_when_you_said_yes(core, tmp_path):
    token = upload_pack(core, own_jar_pack(tmp_path / "p.mrpack"))
    target = tmp_path / "Packed"
    with pytest.raises(ModpackError, match="not checked by a site"):
        await modpack.import_new(
            core, token, name="Packed", directory=str(target), eula_accepted=True
        )
    assert not target.exists()
    await modpack.import_new(
        core, token, name="Packed", directory=str(target), eula_accepted=True, own_jars=True
    )
    assert (target / "mods" / "our-tweaks.jar").read_bytes() == OWN


async def test_importing_own_jars_into_a_server_needs_saying_yes(core, tmp_path):
    ctx = core.servers["survival"]
    token = upload_pack(core, own_jar_pack(tmp_path / "p.mrpack"))
    with pytest.raises(ModpackError, match="not checked by a site"):
        await modpack.import_into(ctx, token)
    assert not (ctx.config.mods_dir / "our-tweaks.jar").exists()
    await modpack.import_into(ctx, token, own_jars=True)
    assert (ctx.config.mods_dir / "our-tweaks.jar").read_bytes() == OWN


def test_a_moderator_without_the_switch_cant_import_own_jars(multi_client, tmp_path):
    owner = dict(multi_client.headers)
    multi_client.headers.pop("Authorization")
    add_helper(multi_client, owner)
    grants = {"grants": {"survival": sorted(P.RUN_IT)}}
    assert multi_client.put("/api/accounts/sam", json=grants, headers=owner).status_code == 200
    sam = sign_in(multi_client, "sam", HELPER_PASSWORD)
    data = own_jar_pack(tmp_path / "p.mrpack").read_bytes()
    read = multi_client.post(
        "/api/modpacks/inspect?server_id=survival",
        files={"file": ("p.mrpack", io.BytesIO(data), "application/zip")},
        headers=sam,
    ).json()
    assert read["pack"]["own_jars"] == ["mods/our-tweaks.jar"]
    refused = multi_client.post(
        "/api/servers/survival/modpack",
        json={"token": read["token"], "confirm": True, "own_jars": True},
        headers=sam,
    )
    assert refused.status_code == 403
    assert "Add own .jar" in refused.json()["detail"]


async def test_modpack_update_asks_again_only_for_new_or_changed_own_jars(
    core, tmp_path, monkeypatch, network
):
    site = FakeModrinth(monkeypatch, network)
    site.publish("v1", "1.0.0", own_jar_pack(tmp_path / "v1.mrpack"), "2026-01-01T00:00:00Z")
    ctx = core.servers["survival"]
    token, _pack = await modpack.from_modrinth(core, "xyz")
    await modpack.import_into(ctx, token, own_jars=True)

    # Same own jar, a new config: nothing to say yes to.
    same = own_jar_pack(tmp_path / "v2.mrpack", **{"overrides/config/new.txt": "x"})
    site.publish("v2", "2.0.0", same, "2026-02-01T00:00:00Z")
    preview = await linkedpack.preview(ctx)
    assert preview["changes"]["new_own_jars"] == []
    await linkedpack.update(ctx, preview["token"])

    # A changed own jar: asked again, and nothing changes without a yes.
    changed = make_pack(
        tmp_path / "v3.mrpack", overrides={"overrides/mods/our-tweaks.jar": OWN + b"!"}
    )
    site.publish("v3", "3.0.0", changed, "2026-03-01T00:00:00Z")
    preview = await linkedpack.preview(ctx)
    assert preview["changes"]["new_own_jars"] == ["mods/our-tweaks.jar"]
    with pytest.raises(ModpackError, match="not checked by a site"):
        await linkedpack.update(ctx, preview["token"])
    assert (ctx.config.mods_dir / "our-tweaks.jar").read_bytes() == OWN
    preview = await linkedpack.preview(ctx)
    await linkedpack.update(ctx, preview["token"], own_jars=True)
    assert (ctx.config.mods_dir / "our-tweaks.jar").read_bytes() == OWN + b"!"


@pytest.mark.parametrize(
    "member", ["overrides/mods/evil.jar.", "overrides/run.bat ", "overrides/config/a.txt:x.jar"]
)
def test_a_name_windows_would_change_blocks_the_pack(tmp_path, member):
    pack = modpack.inspect(make_pack(tmp_path / "p.mrpack", overrides={member: "x"}))
    assert pack.problems and "Windows would change" in pack.problems[0]
    assert pack.to_dict()["can_import"] is False


# ---------------------------------------------------------------- client-overrides
def test_client_overrides_go_to_the_player_only_folder(tmp_path):
    pack = modpack.inspect(
        make_pack(
            tmp_path / "p.mrpack",
            overrides={
                "client-overrides/mods/minimap.jar": OWN,
                "client-overrides/resourcepacks/faithful.zip": "pack",
                "client-overrides/shaderpacks/bsl.zip": "shaders",
                "client-overrides/options.txt": "fov:90",
                "client-overrides/config/minimap.json": "{}",
            },
        )
    )
    assert pack.own_jars == ["player-only/minimap.jar"]
    # The game's own settings and configs aren't the server's business.
    assert "client-overrides" in pack.skipped_overrides
    assert pack.overrides == 3


async def test_a_new_server_keeps_the_players_files_in_the_player_only_folder(core, tmp_path):
    token = upload_pack(
        core,
        make_pack(
            tmp_path / "p.mrpack",
            overrides={
                "client-overrides/resourcepacks/faithful.zip": "pack",
                "client-overrides/options.txt": "fov:90",
            },
        ),
    )
    target = tmp_path / "Packed"
    await modpack.import_new(core, token, name="Packed", directory=str(target), eula_accepted=True)
    assert (target / "player-only" / "faithful.zip").read_text() == "pack"
    assert not (target / "options.txt").exists() and not (target / "resourcepacks").exists()


# ---------------------------------------------------------------- command-block.json
def plugin_pack(path, note, files=None):
    paper_plugin = mod_entry(
        "plugins/luckperms.jar",
        url="https://cdn.modrinth.com/data/Vebnzrzj/versions/a/luckperms.jar",
    )
    return make_pack(
        path,
        files=files if files is not None else [paper_plugin],
        overrides={"server-overrides/command-block.json": note},
    )


NOTE = json.dumps(
    {
        "format": 1,
        "server_type": "paper",
        "plugins": [{"name": "LuckPerms", "site": "Modrinth"}, {"name": "EssentialsX"}],
    }
)


def test_command_block_json_makes_a_plugin_server(tmp_path):
    pack = modpack.inspect(plugin_pack(tmp_path / "p.mrpack", NOTE))
    view = pack.to_dict()
    assert view["type"] == "paper" and view["loader_version"] is None
    # The note itself is read, never written into the server folder.
    assert view["overrides"] == 0
    # Plugins it can't download are listed for the owner to get.
    assert view["plugins_to_get"] == ["EssentialsX"]
    assert view["can_import"] is True


@pytest.mark.parametrize(
    "note, words",
    [
        ("not json", "command-block.json"),
        (json.dumps({"format": 2, "server_type": "paper"}), "command-block.json"),
        (json.dumps({"format": 1, "server_type": "fabric"}), "plugin server"),
        (json.dumps({"format": 1, "server_type": "../evil"}), "server type"),
        (json.dumps({"format": 1, "server_type": "paper", "plugins": "x"}), "command-block.json"),
        (json.dumps({"format": 1, "server_type": "paper", "plugins": [{"name": 5}]}), "json"),
    ],
)
def test_a_bad_command_block_json_is_refused(tmp_path, note, words):
    with pytest.raises(ModpackError, match=words):
        modpack.inspect(plugin_pack(tmp_path / "p.mrpack", note))


def test_plugin_names_are_cut_short_and_capped(tmp_path):
    long_note = json.dumps(
        {
            "format": 1,
            "server_type": "purpur",
            "plugins": [{"name": "x" * 500 + "\n"}] + [{"name": f"p{i}"} for i in range(300)],
        }
    )
    pack = modpack.inspect(plugin_pack(tmp_path / "p.mrpack", long_note))
    assert pack.type_id == "purpur"
    assert len(pack.plugins_to_get) == modpack.MAX_NOTE_PLUGINS
    assert pack.plugins_to_get[0] == "x" * 100


def test_a_curseforge_pack_ignores_command_block_json(tmp_path):
    """Only a .mrpack has server-overrides; a CurseForge zip's file named
    that is just a file (and still never written)."""
    path = tmp_path / "cf.zip"
    manifest = {
        "minecraft": {"version": "1.21.1", "modLoaders": [{"id": "fabric-0.16.5"}]},
        "manifestType": "minecraftModpack",
        "name": "CF",
        "files": [],
        "overrides": "overrides",
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("overrides/command-block.json", NOTE)
    pack = modpack.inspect(path)
    assert pack.type_id == "fabric" and pack.overrides == 0
