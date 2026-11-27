"""Backups and disk: folders left out of backups (with the size preview),
Keep this backup, a backup restored as a new server, and the clean-up with
its bin and Undo. Driven through the agent with the fake Minecraft server.
No network."""

from __future__ import annotations

import json
import os
import time
import zipfile

import pytest

from agent import cleanup, duplicate
from agent.backups.manager import BackupError, check_excludes
from agent.jobs import SUCCEEDED

from .test_api import auth, token_for
from .test_use_case_extras import core, finish  # noqa: F401

WEEK_AGO = time.time() - 8 * 86400


def tiles(base, rel="plugins/dynmap/web/tiles"):
    folder = base.joinpath(*rel.split("/"))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "t.png").write_bytes(b"p" * 5000)
    return folder


def old(path, data=b"old"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (WEEK_AGO, WEEK_AGO))
    return path


# ---------------------------------------------------------------- left out of backups
def test_exclusions_are_checked_paths_and_never_the_world(tmp_path):
    worlds = {"world", "world_nether"}
    assert check_excludes([" plugins\\dynmap\\web\\tiles/ ", ""], tmp_path, worlds) == [
        "plugins/dynmap/web/tiles"
    ]
    assert check_excludes(["world/dimensions/x"], tmp_path, worlds) == ["world/dimensions/x"]
    with pytest.raises(BackupError, match="world"):
        check_excludes(["world"], tmp_path, worlds)
    with pytest.raises(BackupError, match="inside the server folder"):
        check_excludes(["../elsewhere"], tmp_path, worlds)
    with pytest.raises(BackupError):
        check_excludes("plugins", tmp_path, worlds)


async def test_a_left_out_folder_is_not_in_the_backup_and_survives_a_restore(core):  # noqa: F811
    ctx = core.servers["survival"]
    base = ctx.config.server_dir
    folder = tiles(base, "config/squaremap/web/tiles")
    (base / "config" / "keep.yml").write_text("a: 1")
    ctx.config.set("backups.exclude", ["config/squaremap/web/tiles"])

    made = await ctx.backups.create(user="admin")
    with zipfile.ZipFile(ctx.config.backup_dir / made["name"]) as zf:
        names = zf.namelist()
    assert "config/keep.yml" in names
    assert not any("squaremap" in n for n in names)
    assert ctx.backups.get(made["id"])["excludes"] == "config/squaremap/web/tiles"

    # A safety backup (before a change) still holds everything.
    safety = await ctx.backups.create(kind="safety", user="admin")
    with zipfile.ZipFile(ctx.config.backup_dir / safety["name"]) as zf:
        assert any("squaremap" in n for n in zf.namelist())

    (folder / "new.png").write_bytes(b"drawn since")
    await ctx.backups.restore(made["id"], user="admin")
    assert (base / "config" / "keep.yml").read_text() == "a: 1"
    assert (folder / "new.png").read_bytes() == b"drawn since", "today's tiles stay"


async def test_the_size_preview_shows_folders_exclusions_and_map_tiles(core):  # noqa: F811
    ctx = core.servers["survival"]
    base = ctx.config.server_dir
    tiles(base, "config/squaremap/web/tiles")
    before = ctx.backups.size_preview()
    tip = next(s for s in before["suggested"] if s["path"] == "config/squaremap/web/tiles")
    assert tip["name"] == "squaremap" and tip["size_bytes"] == 5000
    assert any(f["path"] == "world" and f["size_bytes"] >= 2048 for f in before["folders"])

    ctx.config.set("backups.exclude", ["config/squaremap/web/tiles"])
    after = ctx.backups.size_preview()
    assert not after["suggested"]
    assert after["excluded"][0]["size_bytes"] == 5000
    assert after["total_bytes"] == before["total_bytes"] - 5000
    assert after["free_bytes"] > 0


def test_exclusions_are_saved_through_server_settings(client):
    token = token_for(client)
    sid = client.get("/api/servers", headers=auth(token)).json()["servers"][0]["id"]
    saved = client.put(
        f"/api/servers/{sid}/settings",
        headers=auth(token),
        json={"updates": {"backups.exclude": ["plugins/dynmap/web/tiles"]}},
    )
    assert saved.status_code == 200, saved.text
    assert "backups.exclude" in saved.json()["applied"], saved.json()
    refused = client.put(
        f"/api/servers/{sid}/settings",
        headers=auth(token),
        json={"updates": {"backups.exclude": ["world"]}},
    ).json()
    assert "backups.exclude" in refused["rejected"]
    preview = client.get(f"/api/servers/{sid}/backups/size", headers=auth(token))
    assert preview.status_code == 200, preview.text
    assert preview.json()["excluded"][0]["path"] == "plugins/dynmap/web/tiles"


# ---------------------------------------------------------------- keep this backup
async def test_a_kept_backup_outlives_the_clean_up(core):  # noqa: F811
    ctx = core.servers["survival"]
    kept = await ctx.backups.create(kind="scheduled", user="admin")
    assert ctx.backups.set_kept(kept["id"], True, user="admin")["kept"] is True
    # Retention keeps one backup a day, so without "keep" this second one
    # would have taken the first one's place.
    gone = await ctx.backups.create(kind="scheduled", user="admin")
    assert ctx.backups.get(kept["id"])
    for span in ("daily", "weekly", "monthly"):
        ctx.config.set(f"backups.keep_{span}", 0)
    await ctx.backups.apply_retention()
    assert ctx.backups.get(kept["id"])
    with pytest.raises(BackupError):
        ctx.backups.get(gone["id"])

    ctx.backups.set_kept(kept["id"], False, user="admin")
    await ctx.backups.apply_retention()
    with pytest.raises(BackupError):
        ctx.backups.get(kept["id"])


def test_keep_answers_through_the_api(client):
    token = token_for(client)
    made = client.post("/api/backups", headers=auth(token), json={}).json()
    answer = client.post(f"/api/backups/{made['id']}/keep", headers=auth(token), json={})
    assert answer.status_code == 200, answer.text
    assert answer.json()["kept"] is True
    listed = client.get("/api/backups", headers=auth(token)).json()["backups"]
    assert next(b for b in listed if b["id"] == made["id"])["kept"] is True
    missing = client.post("/api/backups/9999/keep", headers=auth(token), json={})
    assert missing.status_code == 404


# ---------------------------------------------------------------- restore as a new server
async def test_any_checked_backup_becomes_a_new_server(core, tmp_path):  # noqa: F811
    ctx = core.servers["survival"]
    base = ctx.config.server_dir
    (base / "world" / "level.dat").write_bytes(b"then")
    made = await ctx.backups.create(user="admin")
    (base / "world" / "level.dat").write_bytes(b"now")

    job = await duplicate.start_duplicate(
        core,
        ctx,
        name="Survival then",
        directory=str(tmp_path / "then"),
        world="fresh",
        from_backup=made["id"],
    )
    done = await finish(core, job)
    assert done.state == SUCCEEDED, done.message
    assert (tmp_path / "then" / "world" / "level.dat").read_bytes() == b"then"
    assert (base / "world" / "level.dat").read_bytes() == b"now", "the live server is untouched"


async def test_a_new_server_needs_a_checked_whole_backup(core, tmp_path):  # noqa: F811
    ctx = core.servers["survival"]
    made = await ctx.backups.create(user="admin")
    (ctx.config.backup_dir / made["name"]).write_bytes(b"broken")
    with pytest.raises(duplicate.DuplicateError, match="check"):
        await duplicate.start_duplicate(
            core,
            ctx,
            name="X",
            directory=str(tmp_path / "x"),
            world="fresh",
            from_backup=made["id"],
        )
    (ctx.config.server_dir / "config" / "a.yml").write_text("a")
    one = await ctx.backups.create(includes=["config/a.yml"], user="admin")
    with pytest.raises(duplicate.DuplicateError, match="single files"):
        await duplicate.start_duplicate(
            core, ctx, name="Y", directory=str(tmp_path / "y"), world="fresh", from_backup=one["id"]
        )


# ---------------------------------------------------------------- the clean-up
def fabric_jar(path, mod_id, data_names=()):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "fabric.mod.json", json.dumps({"schemaVersion": 1, "id": mod_id, "version": "1"})
        )
        for name in data_names:
            zf.writestr(f"data/{name}/dimension/x.json", "{}")
    return path


async def test_the_clean_up_lists_only_what_is_safe_to_offer(core):  # noqa: F811
    ctx = core.servers["survival"]
    base = ctx.config.server_dir
    old(base / "logs" / "2026-01-01-1.log.gz")
    (base / "logs" / "today.log.gz").write_bytes(b"new")
    old(base / "logs" / "latest.log")
    old(base / "crash-reports" / "crash-2026.txt")
    old(base / "hs_err_pid42.log")
    (base / "world.replaced-20260101-000000").mkdir()
    (base / "world.replaced-20260101-000000" / "level.dat").write_bytes(b"x" * 100)
    dims = base / "world" / "dimensions"
    for name in ("minecraft", "gone_mod", "kept_mod", "inside_name", "pack_dim"):
        (dims / name / "x").mkdir(parents=True)
    fabric_jar(ctx.config.mods_dir / "kept.jar", "kept_mod", ("inside_name",))
    pack = base / "world" / "datapacks" / "p" / "data" / "pack_dim"
    pack.mkdir(parents=True)
    ctx.mods._cache = None

    found = cleanup.scan(ctx)
    paths = {i["path"]: i["kind"] for i in found["items"]}
    assert paths == {
        "logs/2026-01-01-1.log.gz": "old_log",
        "crash-reports/crash-2026.txt": "crash_report",
        "hs_err_pid42.log": "crash_report",
        "world.replaced-20260101-000000": "set_aside",
        "world/dimensions/gone_mod": "unused_dimension",
    }
    assert found["dimensions_checked"] is True


async def test_an_unreadable_mod_means_no_dimension_is_offered(core):  # noqa: F811
    ctx = core.servers["survival"]
    (ctx.config.server_dir / "world" / "dimensions" / "gone_mod").mkdir(parents=True)
    (ctx.config.mods_dir / "broken.jar").write_bytes(b"not a zip")
    ctx.mods._cache = None
    found = cleanup.scan(ctx)
    assert found["dimensions_checked"] is False
    assert not [i for i in found["items"] if i["kind"] == "unused_dimension"]


async def test_clean_up_goes_to_the_bin_and_undo_puts_it_back(core):  # noqa: F811
    ctx = core.servers["survival"]
    base = ctx.config.server_dir
    log = old(base / "logs" / "a.log.gz", b"x" * 300)
    report = old(base / "crash-reports" / "crash-1.txt")

    with pytest.raises(cleanup.CleanupError, match="isn't on the clean-up list"):
        cleanup.remove(ctx, ["server.properties"])
    done = cleanup.remove(ctx, ["logs/a.log.gz", "crash-reports/crash-1.txt"], user="admin")
    assert done["moved"] == ["logs/a.log.gz", "crash-reports/crash-1.txt"]
    assert not log.exists() and not report.exists()
    assert cleanup.batches(ctx)[0]["items"] == 2

    back = cleanup.undo(ctx, done["batch"], user="admin")
    assert sorted(back["put_back"]) == ["crash-reports/crash-1.txt", "logs/a.log.gz"]
    assert log.read_bytes() == b"x" * 300 and report.exists()
    assert cleanup.batches(ctx) == []


async def test_undo_never_writes_over_a_new_file_and_empty_frees_the_space(core):  # noqa: F811
    ctx = core.servers["survival"]
    log = old(ctx.config.server_dir / "logs" / "b.log.gz")
    done = cleanup.remove(ctx, ["logs/b.log.gz"])
    log.write_bytes(b"made again")
    back = cleanup.undo(ctx, done["batch"])
    assert back["left_in_bin"] == ["logs/b.log.gz"]
    assert log.read_bytes() == b"made again"
    emptied = cleanup.empty(ctx)
    assert emptied["emptied"] == 1 and emptied["freed_bytes"] >= 3
    assert cleanup.batches(ctx) == []


async def test_dimension_data_waits_for_the_server_to_stop_and_old_bins_empty(core):  # noqa: F811
    ctx = core.servers["survival"]
    (ctx.config.server_dir / "world" / "dimensions" / "gone_mod" / "r").mkdir(parents=True)
    ctx.mods._cache = None
    await ctx.server.start()
    try:
        with pytest.raises(cleanup.CleanupError, match="Stop the server"):
            cleanup.remove(ctx, ["world/dimensions/gone_mod"])
    finally:
        await ctx.server.stop()
    done = cleanup.remove(ctx, ["world/dimensions/gone_mod"])
    batch = cleanup.bin_dir(ctx.config) / done["batch"]
    manifest = json.loads((batch / cleanup.MANIFEST).read_text())
    manifest["created_at"] = WEEK_AGO
    (batch / cleanup.MANIFEST).write_text(json.dumps(manifest))
    assert cleanup.empty_old_batches(ctx.config) == 1
    assert not batch.exists()


def test_clean_up_through_the_api(client):
    token = token_for(client)
    sid = client.get("/api/servers", headers=auth(token)).json()["servers"][0]["id"]
    listed = client.get(f"/api/servers/{sid}/cleanup", headers=auth(token))
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == []
    refused = client.post(
        f"/api/servers/{sid}/cleanup", headers=auth(token), json={"paths": ["mods"]}
    )
    assert refused.status_code == 400
    missing = client.post(
        f"/api/servers/{sid}/cleanup/undo", headers=auth(token), json={"batch": "../x"}
    )
    assert missing.status_code == 400
