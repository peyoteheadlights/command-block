"""One copy of each mod file, shared by every server (agent/mods/store.py)."""

import builtins
import hashlib
import io
import os
from pathlib import Path

import pytest

from agent.database.db import Database
from agent.events import EventBus
from agent.minecraft.process import MinecraftServer
from agent.mods import store as store_module
from agent.mods.jarinfo import read_mod_jar
from agent.mods.manager import ModManager
from agent.mods.store import Store
from tests.test_mods import make_jar


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "data" / "agent.db")
    return Store(db, tmp_path / "data" / "files")


def jar_bytes(tmp_path, mod_id, version="1.0.0"):
    path = make_jar(tmp_path / f"src-{mod_id}-{version}.jar", mod_id, version)
    return path.read_bytes()


def manager_for(config, db, server_id="test"):
    db.register_server(server_id, server_id, str(config.server_dir))
    server = MinecraftServer(config, EventBus(), db)
    server.mc_version = "1.20.1"
    (config.server_dir / "libraries").mkdir(exist_ok=True)
    return ModManager(config, EventBus(), db, server)


# ---------------------------------------------------------------- the store
def test_two_servers_share_one_file_and_removing_one_keeps_the_other(store, tmp_path):
    source = make_jar(tmp_path / "sodium.jar", "sodium")
    one, two = tmp_path / "one" / "sodium.jar", tmp_path / "two" / "sodium.jar"
    one.parent.mkdir()
    two.parent.mkdir()

    assert store.place(source, one) is True
    assert store.place(source, two) is True

    stored = store.path_of(hashlib.sha256(source.read_bytes()).hexdigest())
    assert os.path.samefile(one, two) and os.path.samefile(one, stored)
    assert stored.stat().st_nlink == 3  # the store's own name and two servers
    assert len(list(store.root.rglob("*"))) == 2  # one folder, one file

    one.unlink()
    assert two.read_bytes() == source.read_bytes()
    assert stored.stat().st_nlink == 2


def test_a_drive_that_cant_link_gets_a_normal_copy(store, tmp_path, monkeypatch):
    def no_links(*_args, **_kwargs):
        raise OSError("not the same device")

    monkeypatch.setattr(os, "link", no_links)
    source = make_jar(tmp_path / "sodium.jar", "sodium")
    target = tmp_path / "server" / "sodium.jar"
    target.parent.mkdir()

    assert store.place(source, target) is False
    assert target.read_bytes() == source.read_bytes()
    assert target.stat().st_nlink == 1
    assert not list(target.parent.glob(".*"))  # no temp file left behind


def test_find_looks_up_every_hash_the_sites_use(store, tmp_path):
    source = make_jar(tmp_path / "sodium.jar", "sodium")
    data = source.read_bytes()
    store.place(source, tmp_path / "placed.jar")
    stored = store.path_of(hashlib.sha256(data).hexdigest())

    assert store.find(sha1=hashlib.sha1(data).hexdigest()) == stored
    assert store.find(sha512=hashlib.sha512(data).hexdigest().upper()) == stored
    assert store.find(sha256=hashlib.sha256(data).hexdigest()) == stored
    assert store.find(sha1="0" * 40) is None
    assert store.find() is None


def test_a_changed_stored_file_is_never_handed_out(store, tmp_path):
    source = make_jar(tmp_path / "sodium.jar", "sodium")
    sha1 = hashlib.sha1(source.read_bytes()).hexdigest()
    store.place(source, tmp_path / "placed.jar")
    (tmp_path / "placed.jar").unlink()
    stored = store.find(sha1=sha1)
    stored.write_bytes(b"PK changed")

    assert store.find(sha1=sha1) is None


def test_tidy_removes_only_files_nothing_uses(store, tmp_path):
    used = make_jar(tmp_path / "used.jar", "used")
    saved = make_jar(tmp_path / "saved.jar", "saved")
    unused = make_jar(tmp_path / "unused.jar", "unused")
    for jar in (used, saved, unused):
        store.place(jar, jar)  # each one now a link to the store
    store.db.insert(
        "mod_versions",
        {
            "server_id": "test",
            "mod_id": "saved",
            "version": "1.0.0",
            "filename": "saved.jar",
            "archive_path": str(tmp_path / "gone.jar"),
            "sha256": hashlib.sha256(saved.read_bytes()).hexdigest(),
            "created_at": 0,
        },
    )
    saved_sha = hashlib.sha256(saved.read_bytes()).hexdigest()
    unused_sha = hashlib.sha256(unused.read_bytes()).hexdigest()
    saved.unlink()
    unused.unlink()

    assert store.tidy() == 1
    assert not store.path_of(unused_sha).exists()
    assert store.path_of(saved_sha).exists()  # a saved version still needs it
    assert used.exists()
    assert store.find(sha256=unused_sha) is None


def test_usage_reads_real_disk_use_and_what_sharing_saved(store, tmp_path):
    assert store.usage() == {"files": 0, "on_disk": 0, "saved": 0}
    source = make_jar(tmp_path / "sodium.jar", "sodium")
    size = source.stat().st_size
    for name in ("a", "b", "c"):
        store.place(source, tmp_path / f"{name}.jar")

    assert store.usage() == {"files": 1, "on_disk": size, "saved": 2 * size}


def test_a_stored_file_is_locked_once_and_later_ones_catch_up(tmp_path):
    db = Database(tmp_path / "agent.db")
    locked: list[Path] = []
    store = Store(db, tmp_path / "files", lock=None)
    store.place(make_jar(tmp_path / "early.jar", "early"), tmp_path / "early-link.jar")

    store.lock = lambda path: locked.append(path) or True  # the setting turned on
    store.place(make_jar(tmp_path / "late.jar", "late"), tmp_path / "late-link.jar")
    assert len(locked) == 1
    assert store.lock_missing() == 1
    assert len(locked) == 2
    assert store.lock_missing() == 0


# ---------------------------------------------------------------- the mod manager
async def test_every_installed_jar_is_linked_from_the_store(config, tmp_path):
    db = Database(config.database_path)
    manager = manager_for(config, db)
    data = jar_bytes(tmp_path, "cooltech")

    await manager.install_local_file("cooltech.jar", data, user="tester")

    jar = config.mods_dir / "cooltech.jar"
    stored = manager.store.path_of(hashlib.sha256(data).hexdigest())
    assert os.path.samefile(jar, stored)
    # its saved version in mod-backups is the same file too, not a copy
    saved = manager.versions_for("cooltech")[0]["archive_path"]
    assert os.path.samefile(saved, stored)
    assert stored.stat().st_nlink == 3
    assert db.get_setting("mods_stored_separately") is False


async def test_the_mods_page_says_when_a_server_is_on_another_drive(
    tmp_path, monkeypatch, multi_client
):
    def no_links(*_args, **_kwargs):
        raise OSError("not the same device")

    monkeypatch.setattr(os, "link", no_links)
    core = multi_client.app.state.core
    ctx = next(iter(core.servers.values()))
    (ctx.config.server_dir / "libraries").mkdir(exist_ok=True)
    await ctx.mods.install_local_file("cooltech.jar", jar_bytes(tmp_path, "cooltech"), "t")

    got = multi_client.get(f"/api/servers/{ctx.server_id}/mods").json()
    assert got["stored_separately"] is True
    usage = multi_client.get("/api/settings").json()["mod_storage"]
    assert usage["files"] == 1 and usage["saved"] == 0


async def test_a_file_already_in_the_store_is_not_downloaded_again(config, tmp_path):
    db = Database(config.database_path)
    manager = manager_for(config, db)
    data = jar_bytes(tmp_path, "cooltech")
    source = tmp_path / "cooltech-src.jar"
    source.write_bytes(data)
    manager.store.place(source, tmp_path / "elsewhere.jar")

    class Site:
        async def download(self, version):
            raise AssertionError("downloaded a file the store already had")

    version = {
        "file": {"filename": "cooltech.jar", "sha1": hashlib.sha1(data).hexdigest()},
    }
    got, name, sha256 = await manager._download(Site(), version)
    assert (got, name, sha256) == (data, "cooltech.jar", hashlib.sha256(data).hexdigest())


async def test_no_code_path_writes_a_placed_jar_in_place(config, tmp_path, monkeypatch):
    """Install, replace, update-style swap, turn off and on, remove and go
    back to an older version: none of them may open a stored file for
    writing, or every server sharing it would change."""
    db = Database(config.database_path)
    manager = manager_for(config, db)
    v1, v2 = jar_bytes(tmp_path, "cooltech", "1.0.0"), jar_bytes(tmp_path, "cooltech", "2.0.0")

    stored_inodes: set[tuple[int, int]] = set()
    real_open = builtins.open

    def watched_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, Path)) and any(m in mode for m in "wax+"):
            try:
                info = os.stat(file)
            except OSError:
                info = None
            if info and (info.st_dev, info.st_ino) in stored_inodes:
                raise AssertionError(f"{file} is a shared file and was opened to write")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", watched_open)
    monkeypatch.setattr(io, "open", watched_open)

    def remember_store():
        for path in manager.store.root.rglob("*"):
            if path.is_file():
                info = path.stat()
                stored_inodes.add((info.st_dev, info.st_ino))

    await manager.install_local_file("cooltech.jar", v1, user="t")
    remember_store()
    await manager.install_local_file("cooltech.jar", v2, user="t", allow_replace=True)
    remember_store()
    await manager.set_enabled("cooltech.jar", False, user="t")
    await manager.set_enabled("cooltech.jar.disabled", True, user="t")
    old = next(v for v in manager.versions_for("cooltech") if v["version"] == "1.0.0")
    await manager.rollback("cooltech", old["archive_path"], user="t")
    remember_store()
    assert read_mod_jar(config.mods_dir / "cooltech.jar").version == "1.0.0"
    await manager.remove("cooltech.jar", user="t")
    assert stored_inodes  # the check really watched something


# ---------------------------------------------------------------- the daily job
async def test_share_links_identical_jars_on_stopped_servers_only(multi, tmp_path, monkeypatch):
    from agent.core import AgentCore

    monkeypatch.setattr(store_module, "PAUSE", 0)
    core = AgentCore(multi)
    one, two = list(core.servers.values())[:2]
    for ctx in (one, two):
        make_jar(ctx.config.mods_dir / "sodium.jar", "sodium")
    monkeypatch.setattr(type(two.server), "running", property(lambda s: s is two.server))

    await store_module.upkeep(core)

    stored = one.mods.store.path_of(
        hashlib.sha256((one.config.mods_dir / "sodium.jar").read_bytes()).hexdigest()
    )
    assert os.path.samefile(one.config.mods_dir / "sodium.jar", stored)
    assert not os.path.samefile(two.config.mods_dir / "sodium.jar", stored)  # running

    monkeypatch.setattr(type(two.server), "running", property(lambda s: False))
    await store_module.upkeep(core)
    assert os.path.samefile(two.config.mods_dir / "sodium.jar", stored)
    assert await store_module.share(two) == 0  # nothing left to link
    core.db.close()


async def test_tidy_waits_while_a_job_runs(multi, monkeypatch):
    from agent.core import AgentCore

    core = AgentCore(multi)
    tidied = []
    monkeypatch.setattr(Store, "tidy", lambda self: tidied.append(1) or 0)
    monkeypatch.setattr(core.jobs, "recent", lambda **_kw: ["a job"])
    await store_module.upkeep(core)
    assert tidied == []
    monkeypatch.setattr(core.jobs, "recent", lambda **_kw: [])
    await store_module.upkeep(core)
    assert tidied == [1]
    core.db.close()
