# ruff: noqa: F811  (fixtures imported by name)
"""Server / Players / Both on the Mods page (agent/mods/sides.py).

Each mod is marked with who needs it, and says where the mark came from.
Players-only things live in the player-only folder, which the server never
loads. Moving a mod there and back needs the server stopped. Odd setups (a
mod that needs one on the other side) are warned about."""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from agent.minecraft.properties import FormError
from agent.mods import sides
from agent.mods.manager import ModError
from agent.mods.modrinth import ModrinthError
from agent.security.paths import PathSafetyError

from .test_mods import make_jar, manager  # noqa: F401  (fixture)


@pytest.fixture
def ctx(manager):
    manager.mods_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(mods=manager, db=manager.db, config=manager.config, server_id="test")


@pytest.fixture(autouse=True)
def listed(monkeypatch):
    """Every file is one a site lists, unless a test says otherwise."""

    async def known(mods, data):
        return "modrinth", {"project_id": "x"}

    monkeypatch.setattr(sides, "known_source", known)


def marks(ctx):
    rows = ctx.mods.list_installed()
    sides.add_marks(ctx, rows)
    return {r["mod_id"] or r["filename"]: (r["side"], r["side_from"]) for r in rows}


def zip_bytes(files: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return out.getvalue()


def jar_bytes(tmp_path, mod_id, **kw) -> bytes:
    return make_jar(tmp_path / f"{mod_id}-src.jar", mod_id, **kw).read_bytes()


# ---------------------------------------------------------------- where marks come from
def test_a_mark_comes_from_the_jar_and_says_so(ctx):
    folder = ctx.mods.mods_dir
    make_jar(folder / "a.jar", "minimap", environment="client")
    make_jar(folder / "b.jar", "chunky", environment="server")
    make_jar(folder / "c.jar", "create")
    make_jar(folder / "d.jar", "forgething", loader="forge")
    found = marks(ctx)
    assert found["minimap"] == ("players", "file")
    assert found["chunky"] == ("server", "file")
    assert found["create"] == ("both", "file")
    # A Forge jar doesn't say: shown as Both, marked as not sure.
    assert found["d"] == ("both", "unknown")


async def test_the_sites_sides_beat_the_jar_and_the_owners_beat_both(ctx):
    make_jar(ctx.mods.mods_dir / "lithium.jar", "lithium")
    ctx.mods.db.set_setting(
        "mod_source:lithium",
        {
            "source": "modrinth",
            "modrinth_project": "gvQq",
            "client_side": "optional",
            "server_side": "required",
        },
    )
    assert marks(ctx)["lithium"] == ("server", "modrinth")
    await sides.set_side(ctx, "lithium.jar", "both", "owner")
    assert marks(ctx)["lithium"] == ("both", "you")


def test_a_curseforge_mod_tagged_for_the_game_only_is_marked_players(ctx):
    make_jar(ctx.mods.mods_dir / "jei.jar", "jei")
    ctx.mods.remember("jei", "curseforge", {"project_id": "curseforge:1", "client_only": True})
    assert marks(ctx)["jei"] == ("players", "curseforge")


def test_the_linked_modpacks_own_env_is_used_for_its_files(ctx):
    path = make_jar(ctx.mods.mods_dir / "sodium.jar", "sodium")
    from agent.mods.jarinfo import sha256_file

    link = {
        "site": "modrinth",
        "project": "p",
        "version_id": "v",
        "overrides": {},
        "files": [
            {"path": "mods/sodium.jar", "key": "k", "sha256": sha256_file(path), "side": "players"}
        ],
    }
    (ctx.config.server_dir / "command-block-modpack.json").write_text(json.dumps(link))
    assert marks(ctx)["sodium"] == ("players", "modpack")


@pytest.mark.parametrize(
    "client,server,side",
    [
        ("required", "required", "both"),
        ("optional", "optional", "both"),
        ("required", "unsupported", "players"),
        ("unsupported", "required", "server"),
        ("optional", "required", "server"),
        ("required", "optional", "players"),
        (None, "required", None),
    ],
)
def test_modpack_and_site_sides_read_the_same_way(client, server, side):
    assert sides.from_pack({"client": client, "server": server}) == side


async def test_modrinth_is_asked_once_for_the_sides_it_knows(ctx):
    make_jar(ctx.mods.mods_dir / "lithium.jar", "lithium")
    ctx.mods.remember("lithium", "modrinth", {"project_id": "gvQq", "version_id": "v1"})
    asked = []

    async def projects(ids):
        asked.append(ids)
        return {"gvQq": {"client_side": "unsupported", "server_side": "required"}}

    ctx.mods.modrinth.projects = projects
    await sides.catch_up(ctx.mods)
    await sides.catch_up(ctx.mods)
    assert asked == [["gvQq"]]
    assert marks(ctx)["lithium"] == ("server", "modrinth")


async def test_when_modrinth_cant_answer_the_mark_falls_back_to_the_file(ctx):
    make_jar(ctx.mods.mods_dir / "lithium.jar", "lithium", environment="server")
    ctx.mods.remember("lithium", "modrinth", {"project_id": "gvQq", "version_id": "v1"})

    async def down(ids):
        raise ModrinthError("down")

    ctx.mods.modrinth.projects = down
    await sides.catch_up(ctx.mods)
    assert marks(ctx)["lithium"] == ("server", "file")


# ---------------------------------------------------------------- changing a mark
async def test_marking_players_moves_the_jar_out_and_both_moves_it_back(ctx):
    make_jar(ctx.mods.mods_dir / "minimap.jar", "minimap")
    result = await sides.set_side(ctx, "minimap.jar", "players", "owner")
    assert result["moved"] is True
    assert not (ctx.mods.mods_dir / "minimap.jar").exists()
    assert (ctx.config.server_dir / "player-only" / "minimap.jar").is_file()
    assert "minimap" not in marks(ctx)  # the server no longer loads it
    assert [f["mod_id"] for f in sides.player_files(ctx)] == ["minimap"]

    await sides.set_side(ctx, "minimap.jar", "both", "owner")
    assert (ctx.mods.mods_dir / "minimap.jar").is_file()
    assert sides.player_files(ctx) == []
    assert marks(ctx)["minimap"] == ("both", "you")


async def test_a_turned_off_mod_moves_as_its_plain_name(ctx):
    make_jar(ctx.mods.mods_dir / "minimap.jar.disabled", "minimap")
    result = await sides.set_side(ctx, "minimap.jar.disabled", "players", "owner")
    assert result["filename"] == "minimap.jar"
    assert (ctx.config.server_dir / "player-only" / "minimap.jar").is_file()


async def test_server_and_both_only_change_the_mark(ctx):
    make_jar(ctx.mods.mods_dir / "a.jar", "alpha")
    result = await sides.set_side(ctx, "a.jar", "server", "owner")
    assert result["moved"] is False
    assert marks(ctx)["alpha"] == ("server", "you")


async def test_moving_needs_the_server_stopped(ctx, monkeypatch):
    make_jar(ctx.mods.mods_dir / "a.jar", "alpha")
    monkeypatch.setattr(type(ctx.mods.server), "running", property(lambda self: True))
    with pytest.raises(ModError, match="Stop it before"):
        await sides.set_side(ctx, "a.jar", "players", "owner")
    assert (ctx.mods.mods_dir / "a.jar").is_file()
    await sides.set_side(ctx, "a.jar", "server", "owner")  # only the mark: no stop needed


async def test_a_wrong_side_name_and_a_path_are_refused(ctx):
    make_jar(ctx.mods.mods_dir / "a.jar", "alpha")
    with pytest.raises(ModError):
        await sides.set_side(ctx, "a.jar", "everyone", "owner")
    with pytest.raises(PathSafetyError):
        await sides.set_side(ctx, "../a.jar", "players", "owner")


async def test_a_pack_for_players_cant_move_onto_the_server(ctx, tmp_path):
    await sides.add(ctx, "shiny.zip", zip_bytes({"pack.mcmeta": "{}"}), "owner", own_jar=True)
    with pytest.raises(ModError, match="pack for players"):
        await sides.set_side(ctx, "shiny.zip", "both", "owner")


async def test_a_mod_for_another_loader_stays_with_players(ctx, tmp_path):
    folder = ctx.config.server_dir / "player-only"
    folder.mkdir()
    make_jar(folder / "forgey.jar", "forgey", loader="forge")
    with pytest.raises(ModError):
        await sides.set_side(ctx, "forgey.jar", "both", "owner")
    assert (folder / "forgey.jar").is_file()


# ---------------------------------------------------------------- adding for players
async def test_mods_resource_packs_and_shader_packs_can_be_added_for_players(ctx, tmp_path):
    await sides.add(ctx, "map.jar", jar_bytes(tmp_path, "minimap"), "owner", own_jar=True)
    await sides.add(ctx, "faithful.zip", zip_bytes({"pack.mcmeta": "{}"}), "owner", own_jar=True)
    done = await sides.add(
        ctx, "bsl.zip", zip_bytes({"shaders/final.fsh": "x"}), "owner", own_jar=True
    )
    assert done["kind"] == "shader pack"
    found = {f["filename"]: f["kind"] for f in sides.player_files(ctx)}
    assert found == {"map.jar": "mod", "faithful.zip": "pack", "bsl.zip": "pack"}
    assert not (ctx.mods.mods_dir / "map.jar").exists()  # never where the server loads


async def test_anything_else_is_refused(ctx, tmp_path):
    with pytest.raises(ModError):
        await sides.add(ctx, "notes.zip", zip_bytes({"readme.txt": "hi"}), "owner", own_jar=True)
    with pytest.raises(ModError):
        await sides.add(ctx, "pack.jar", zip_bytes({"pack.mcmeta": "{}"}), "owner", own_jar=True)
    with pytest.raises(PathSafetyError):
        await sides.add(ctx, "run.exe", b"PK..", "owner", own_jar=True)
    with pytest.raises(PathSafetyError):
        await sides.add(ctx, "../x.zip", zip_bytes({"pack.mcmeta": "{}"}), "owner", own_jar=True)
    with pytest.raises(ModError, match="already"):
        await sides.add(ctx, "a.zip", zip_bytes({"pack.mcmeta": "{}"}), "owner", own_jar=True)
        await sides.add(ctx, "a.zip", zip_bytes({"pack.mcmeta": "{}"}), "owner", own_jar=True)
    assert [f["filename"] for f in sides.player_files(ctx)] == ["a.zip"]


async def test_a_mod_for_another_loader_or_a_plugin_is_refused(ctx, tmp_path):
    with pytest.raises(ModError):
        await sides.add(
            ctx, "f.jar", jar_bytes(tmp_path, "f", loader="forge"), "owner", own_jar=True
        )
    plugin = zip_bytes({"plugin.yml": "name: P\nversion: 1\nmain: a.B\n"})
    with pytest.raises(ModError):
        await sides.add(ctx, "p.jar", plugin, "owner", own_jar=True)


async def test_a_file_no_site_lists_needs_a_yes_first(ctx, tmp_path, monkeypatch):
    async def unknown(mods, data):
        return None

    monkeypatch.setattr(sides, "known_source", unknown)
    data = jar_bytes(tmp_path, "own")
    with pytest.raises(FormError):
        await sides.add(ctx, "own.jar", data, "owner", own_jar=True)
    assert sides.player_files(ctx) == []
    await sides.add(ctx, "own.jar", data, "owner", confirm=True, own_jar=True)
    assert [f["filename"] for f in sides.player_files(ctx)] == ["own.jar"]


async def test_removing_a_player_file_moves_it_to_the_trash(ctx):
    await sides.add(ctx, "a.zip", zip_bytes({"pack.mcmeta": "{}"}), "owner", own_jar=True)
    await sides.remove(ctx, "a.zip", "owner")
    assert sides.player_files(ctx) == []
    assert any(p.name.endswith("-a.zip") for p in ctx.mods.trash_dir.iterdir())
    with pytest.raises(ModError):
        await sides.remove(ctx, "a.zip", "owner")


# ---------------------------------------------------------------- odd setups
def odd(ctx):
    rows = ctx.mods.list_installed()
    sides.add_marks(ctx, rows)
    return [w["text"] for w in sides.odd_setups(ctx, rows)]


async def test_a_server_mod_that_needs_a_players_only_mod_is_warned_about(ctx):
    make_jar(ctx.mods.mods_dir / "a.jar", "alpha", name="Alpha", depends={"beta": "*"})
    make_jar(ctx.mods.mods_dir / "b.jar", "beta", name="Beta")
    assert odd(ctx) == []
    await sides.set_side(ctx, "b.jar", "players", "owner")
    assert odd(ctx) == [
        "Alpha runs on the server and needs Beta, which is marked Players. Mark it Both, "
        "or the server may not start."
    ]


async def test_a_players_mod_that_needs_a_server_only_mod_is_warned_about(ctx):
    make_jar(ctx.mods.mods_dir / "a.jar", "alpha", name="Alpha", depends={"beta": "*"})
    make_jar(ctx.mods.mods_dir / "b.jar", "beta", name="Beta")
    await sides.set_side(ctx, "b.jar", "server", "owner")
    assert odd(ctx) == [
        "Alpha is for players and needs Beta, which is marked Server. Players won't get it "
        "in the modpack. Mark it Both."
    ]
    await sides.set_side(ctx, "b.jar", "both", "owner")
    assert odd(ctx) == []


def test_minecraft_and_the_loader_are_never_odd(ctx):
    make_jar(ctx.mods.mods_dir / "a.jar", "alpha", depends={"minecraft": "*", "fabricloader": "*"})
    assert odd(ctx) == []


# ---------------------------------------------------------------- the routes the apps call
def test_the_mods_page_gets_marks_player_files_and_warnings(client, config):
    from .test_api import auth, token_for

    (config.server_dir / "libraries").mkdir()
    make_jar(config.mods_dir / "a.jar", "alpha", name="Alpha", depends={"beta": "*"})
    make_jar(config.mods_dir / "b.jar", "beta", name="Beta", environment="client")
    headers = auth(token_for(client))

    moved = client.post(
        "/api/mods/side", json={"filename": "b.jar", "side": "players"}, headers=headers
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["moved"] is True
    added = client.post(
        "/api/mods/player-only/upload",
        files={"file": ("faithful.zip", zip_bytes({"pack.mcmeta": "{}"}))},
        headers=headers,
    )
    assert added.status_code == 200, added.text

    page = client.get("/api/mods", headers=headers).json()
    assert [(m["filename"], m["side"], m["side_from"]) for m in page["mods"]] == [
        ("a.jar", "both", "file")
    ]
    assert [f["filename"] for f in page["player_only"]] == ["b.jar", "faithful.zip"]
    assert len(page["odd_setups"]) == 1

    bad = client.post("/api/mods/side", json={"filename": "a.jar", "side": "all"}, headers=headers)
    assert bad.status_code == 422
    gone = client.post(
        "/api/mods/player-only/remove", json={"filename": "faithful.zip"}, headers=headers
    )
    assert gone.status_code == 200, gone.text


def test_the_link_remembers_the_modpacks_own_side_for_each_file(tmp_path):
    from agent import linkedpack, modpack

    (tmp_path / "mods").mkdir()
    (tmp_path / "mods" / "chunky.jar").write_bytes(b"PK chunky")
    item = modpack.PackFile(
        path="mods/chunky.jar",
        name="chunky.jar",
        size=9,
        status=modpack.DOWNLOAD,
        server="required",
        client="unsupported",
        key="k" * 128,
    )
    assert linkedpack._file_entry(tmp_path, item)["side"] == "server"


# ---------------------------------------------------------------- marks in the server folder
async def test_marks_live_in_a_file_in_the_server_folder_and_survive_an_update(ctx):
    make_jar(ctx.mods.mods_dir / "alpha-1.0.jar", "alpha")
    await sides.set_side(ctx, "alpha-1.0.jar", "server", "owner")
    saved = json.loads((ctx.config.server_dir / sides.MARKS_FILE).read_text())
    assert saved == {"alpha": "server"}
    (ctx.mods.mods_dir / "alpha-1.0.jar").unlink()
    make_jar(ctx.mods.mods_dir / "alpha-2.0.jar", "alpha", version="2.0")
    assert marks(ctx)["alpha"] == ("server", "you")  # kept by mod id


async def test_two_jars_with_one_id_get_their_own_marks(ctx):
    make_jar(ctx.mods.mods_dir / "a1.jar", "alpha")
    make_jar(ctx.mods.mods_dir / "a2.jar", "alpha")
    await sides.set_side(ctx, "a2.jar", "server", "owner")
    rows = ctx.mods.list_installed()
    sides.add_marks(ctx, rows)
    found = {r["filename"]: r["side_from"] for r in rows}
    assert found == {"a1.jar": "file", "a2.jar": "you"}


def test_a_broken_or_odd_marks_file_is_left_out(ctx):
    make_jar(ctx.mods.mods_dir / "a.jar", "alpha")
    path = ctx.config.server_dir / sides.MARKS_FILE
    path.write_text("not json")
    assert marks(ctx)["alpha"] == ("both", "file")
    path.write_text(json.dumps({"alpha": "everyone", "x" * 400: "server"}))
    assert sides.read_marks(ctx.config) == {}
    path.write_text(json.dumps(["alpha"]))
    assert sides.read_marks(ctx.config) == {}


def test_backups_take_the_marks_and_the_folder_unless_turned_off(ctx):
    assert sides.backup_names(ctx.db) == [sides.MARKS_FILE, sides.FOLDER]
    sides.set_options(ctx, backup=False, skip_listed=False)
    assert sides.backup_names(ctx.db) == [sides.MARKS_FILE]


# ---------------------------------------------------------------- the folder kept safe
async def test_the_folder_is_locked_from_the_servers_user_when_that_is_on(
    ctx, monkeypatch, tmp_path
):
    from agent import serveruser

    locked = []
    monkeypatch.setattr(serveruser, "is_on", lambda config: True)
    monkeypatch.setattr(serveruser, "deny_all", lambda path: locked.append(path) or True)
    await sides.add(ctx, "a.zip", zip_bytes({"pack.mcmeta": "{}"}), "owner", own_jar=True)
    assert locked == [ctx.config.server_dir / sides.FOLDER]
    sides.lock(ctx.config)  # before each server start
    assert len(locked) == 2


async def test_a_file_changed_outside_the_app_is_pointed_out(ctx):
    await sides.add(ctx, "a.zip", zip_bytes({"pack.mcmeta": "{}"}), "owner", own_jar=True)
    assert [f["changed"] for f in sides.player_files(ctx)] == [False]
    path = ctx.config.server_dir / sides.FOLDER / "a.zip"
    path.write_bytes(zip_bytes({"pack.mcmeta": "{}", "evil.txt": "x"}))
    assert [f["changed"] for f in sides.player_files(ctx)] == [True]
    # One first seen here (a copied server) is taken as it is, then watched.
    other = ctx.config.server_dir / sides.FOLDER / "b.zip"
    other.write_bytes(zip_bytes({"pack.mcmeta": "{}"}))
    assert {f["filename"]: f["changed"] for f in sides.player_files(ctx)}["b.zip"] is False
    other.write_bytes(zip_bytes({"pack.mcmeta": "{}", "x": "y"}))
    assert {f["filename"]: f["changed"] for f in sides.player_files(ctx)}["b.zip"] is True


async def test_touching_a_file_without_changing_it_isnt_a_change(ctx):
    import os

    await sides.add(ctx, "a.zip", zip_bytes({"pack.mcmeta": "{}"}), "owner", own_jar=True)
    path = ctx.config.server_dir / sides.FOLDER / "a.zip"
    os.utime(path, (1, 1))
    assert [f["changed"] for f in sides.player_files(ctx)] == [False]


async def test_files_modrinth_lists_can_be_left_out_and_got_again(ctx, monkeypatch):
    import hashlib

    from agent import downloads

    data = zip_bytes({"pack.mcmeta": "{}"})

    async def known(mods, sent):
        return ("modrinth", {"project_id": "x"}) if sent == data else None

    monkeypatch.setattr(sides, "known_source", known)
    await sides.add(ctx, "faithful.zip", data, "owner", own_jar=True)
    await sides.add(
        ctx, "mine.zip", zip_bytes({"shaders/a.fsh": "x"}), "owner", confirm=True, own_jar=True
    )
    path = ctx.config.server_dir / sides.FOLDER / "faithful.zip"
    assert sides.backup_skips(ctx.config, ctx.db) == set()
    sides.set_options(ctx, backup=True, skip_listed=True)
    assert sides.backup_skips(ctx.config, ctx.db) == {path}  # mine.zip is still backed up

    path.unlink()  # as after a restore that left it out
    missing = {f["filename"]: f for f in sides.player_files(ctx)}["faithful.zip"]
    assert missing["missing"] is True and missing["site"] == "modrinth"
    sha512 = hashlib.sha512(data).hexdigest()

    async def file_url(sha):
        assert sha == sha512
        return "https://cdn.modrinth.com/data/x/versions/y/faithful.zip"

    async def fetched(spec):
        assert spec.sha512 == sha512 and spec.allow_unverified is False
        return data, None

    ctx.mods.modrinth.file_url = file_url
    monkeypatch.setattr(downloads, "download_bytes", fetched)
    await sides.fetch_again(ctx, "faithful.zip", "owner")
    assert path.read_bytes() == data
    assert [f["missing"] for f in sides.player_files(ctx)] == [False, False]


async def test_only_a_missing_modrinth_file_is_got_again(ctx, monkeypatch):
    async def unknown(mods, data):
        return None

    monkeypatch.setattr(sides, "known_source", unknown)
    await sides.add(
        ctx, "mine.zip", zip_bytes({"pack.mcmeta": "{}"}), "owner", confirm=True, own_jar=True
    )
    with pytest.raises(ModError, match="isn't missing"):
        await sides.fetch_again(ctx, "mine.zip", "owner")
    (ctx.config.server_dir / sides.FOLDER / "mine.zip").unlink()
    with pytest.raises(ModError, match="isn't on Modrinth"):
        await sides.fetch_again(ctx, "mine.zip", "owner")
    with pytest.raises(PathSafetyError):
        await sides.fetch_again(ctx, "../x.zip", "owner")


async def test_removing_forgets_the_file(ctx):
    await sides.add(ctx, "a.zip", zip_bytes({"pack.mcmeta": "{}"}), "owner", own_jar=True)
    await sides.remove(ctx, "a.zip", "owner")
    assert sides.player_files(ctx) == []  # not shown as missing


async def test_a_real_backup_holds_the_marks_and_player_files_and_skips_listed_ones(ctx):
    from agent.backups.manager import BackupManager

    mods = ctx.mods
    make_jar(mods.mods_dir / "a.jar", "alpha")
    await sides.set_side(ctx, "a.jar", "server", "owner")
    await sides.add(ctx, "faithful.zip", zip_bytes({"pack.mcmeta": "{}"}), "owner", own_jar=True)
    backups = BackupManager(mods.config, mods.bus, mods.db, mods.server)

    def names(result):
        with zipfile.ZipFile(result["path"]) as zf:
            return set(zf.namelist())

    held = names(await backups.create(user="owner"))
    assert sides.MARKS_FILE in held and "player-only/faithful.zip" in held
    sides.set_options(ctx, backup=True, skip_listed=True)
    assert "player-only/faithful.zip" not in names(await backups.create(user="owner"))
    sides.set_options(ctx, backup=False, skip_listed=False)
    held = names(await backups.create(user="owner"))
    assert sides.MARKS_FILE in held and not any(n.startswith("player-only") for n in held)
