"""Bringing a server in from a folder or a zip: the type is found from its
files (start scripts read as text, never run), what was found is shown
first, it's copied as one job (the original never changes), a port clash
is fixed, and the first backup is made."""

from __future__ import annotations

import os
import stat
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agent import bringin, downloads
from agent.core import AgentCore
from agent.jobs import RUNNING, SUCCEEDED

from .conftest import build_multi_config
from .test_multi_server import wait_for
from .test_server_types import SERVER_JAR, fake_installer, fake_sources  # noqa: F401

ARGS = "win_args.txt" if os.name == "nt" else "unix_args.txt"

# Each type as a folder holds it: {path inside: bytes or text}.
LAYOUTS = {
    "vanilla": {"server.jar": SERVER_JAR, "versions/1.21.1/server-1.21.1.jar": SERVER_JAR},
    "fabric": {
        "fabric-server-launch.jar": SERVER_JAR,
        "server.jar": SERVER_JAR,
        "mods/a.jar": b"m",
    },
    "quilt": {"quilt-server-launch.jar": SERVER_JAR, "mods/a.jar": b"m"},
    "forge": {
        f"libraries/net/minecraftforge/forge/1.20.1-47.3.0/{ARGS}": "-p a.jar",
        "run.bat": "",
    },
    "neoforge": {f"libraries/net/neoforged/neoforge/21.1.9/{ARGS}": "-p a.jar"},
    "paper": {
        "paper-1.21.1-129.jar": SERVER_JAR,
        "version_history.json": '{"currentVersion": "git-Paper-129 (MC: 1.21.1)"}',
        "plugins/one.jar": b"p",
        "plugins/two.jar": b"p",
    },
    "purpur": {"purpur-1.21.1.jar": SERVER_JAR, "purpur.yml": "settings: {}"},
    "leaf": {"leaf-1.21.1-12.jar": SERVER_JAR, "config/leaf-global.yml": "x: 1"},
    "bedrock": {"bedrock_server.exe": b"MZ", "worlds/Bedrock level/level.dat": b"x"},
}


def write(folder: Path, files: dict) -> Path:
    for name, data in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
    return folder


def zipped(path: Path, files: dict, root: str = "") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(f"{root}{name}", data)
    return path


def snapshot(folder: Path) -> dict:
    return {
        p.relative_to(folder).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in folder.rglob("*")
        if p.is_file()
    }


@pytest.fixture
def network(monkeypatch):
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(fake_sources()))


@pytest.fixture
async def core(tmp_path, network):
    agent = AgentCore(build_multi_config(tmp_path))
    yield agent
    await agent.jobs.stop()
    agent.db.close()


async def finish(core, job):
    assert await wait_for(lambda: core.jobs.get(job.id).state != RUNNING, 30)
    return core.jobs.get(job.id)


# ---------------------------------------------------------------- reading
@pytest.mark.parametrize("type_id", sorted(LAYOUTS))
def test_each_type_is_found_from_its_files(core, tmp_path, type_id):
    folder = write(tmp_path / "old", {**LAYOUTS[type_id], "world/level.dat": b"x"})
    found = bringin.inspect(core, str(folder))
    assert found.type == type_id and found.problem is None


def test_the_summary_says_what_was_found_before_anything_is_copied(core, tmp_path):
    folder = write(tmp_path / "old", {**LAYOUTS["paper"], "world/level.dat": b"x"})
    found = bringin.inspect(core, str(folder)).to_dict()
    assert found["summary"] == "Paper 1.21.1, 2 plugins, 1 world"
    assert found["jar"] == "paper-1.21.1-129.jar" and found["can_bring_in"]
    assert not (tmp_path / "new").exists()


def test_an_unknown_kind_lists_its_jars(core, tmp_path):
    folder = write(tmp_path / "old", {"myfork.jar": SERVER_JAR, "world/level.dat": b"x"})
    found = bringin.inspect(core, str(folder))
    assert found.type is None and found.jars == ["myfork.jar"] and found.problem is None


def test_a_folder_with_no_server_in_it_says_so(core, tmp_path):
    folder = write(tmp_path / "photos", {"cat.png": b"x"})
    assert "no server jar" in bringin.inspect(core, str(folder)).problem


def test_a_registered_servers_own_folder_points_to_duplicate(core):
    folder = core.servers["survival"].config.server_dir
    with pytest.raises(bringin.BringInError, match="Duplicate"):
        bringin.inspect(core, str(folder))


def test_a_curseforge_server_zip_is_read_from_its_start_files(core, tmp_path):
    pack = zipped(
        tmp_path / "Pack-Server.zip",
        {
            "variables.txt": "MINECRAFT_VERSION=1.20.1\nMODLOADER=Forge\nMODLOADER_VERSION=47.2.0\n",
            "startserver.bat": "@echo off\njava -jar forge-installer.jar --installServer\n",
            "mods/jei.jar": b"m",
            "mods/create.jar": b"m",
            "config/jei.toml": "a = 1",
        },
        root="Pack-Server/",
    )
    found = bringin.inspect(core, str(pack))
    assert (found.type, found.minecraft, found.loader) == ("forge", "1.20.1", "1.20.1-47.2.0")
    assert found.needs_install and found.addons == 2 and found.source == "zip"


@pytest.mark.parametrize(
    "files",
    [{"../outside.txt": "x", "server.jar": SERVER_JAR}, {"C:/Windows/x.txt": "x"}],
)
def test_a_zip_with_paths_out_of_its_folder_is_refused(core, tmp_path, files):
    with pytest.raises(bringin.BringInError, match="isn't safe"):
        bringin.inspect(core, str(zipped(tmp_path / "bad.zip", files)))


def test_a_zip_holding_a_link_is_refused(core, tmp_path):
    path = tmp_path / "link.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("server.jar", SERVER_JAR)
        link = zipfile.ZipInfo("world")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "/etc")
    with pytest.raises(bringin.BringInError, match="isn't safe"):
        bringin.inspect(core, str(path))


# ---------------------------------------------------------------- bringing in
async def test_a_folder_is_copied_registered_backed_up_and_left_as_it_was(core, tmp_path):
    taken = core.servers["survival"].config.server.port
    folder = write(
        tmp_path / "old",
        {
            **LAYOUTS["paper"],
            "world/level.dat": b"x" * 100,
            "server.properties": f"server-port={taken}\nmotd=Old\n",
            "logs/latest.log": "old log",
        },
    )
    before = snapshot(folder)
    job = await bringin.bring_in(
        core, path=str(folder), name="Old SMP", directory=str(tmp_path / "new"), user="admin"
    )
    done = await finish(core, job)
    assert done.state == SUCCEEDED, done.message
    assert snapshot(folder) == before, "the original is never changed"
    ctx = core.get_server("old-smp")
    assert (ctx.config.server.type, ctx.config.server.jar) == ("paper", "paper-1.21.1-129.jar")
    assert ctx.config.server.custom_jar is False
    assert (tmp_path / "new" / "plugins" / "two.jar").is_file()
    assert not (tmp_path / "new" / "logs").exists(), "the old place's logs stay behind"
    assert ctx.config.server.port != taken
    assert (
        f"server-port={ctx.config.server.port}"
        in (tmp_path / "new" / "server.properties").read_text()
    )
    assert "used by" in done.result["port_note"]
    # its files said 1.21.1: pending until the console prints it
    assert ctx.server.status()["minecraft_version"] is None
    assert ctx.server.pending_version["minecraft_version"] == "1.21.1"
    assert [b["name"] for b in ctx.backups.list_backups()] and done.result["backup"]


async def test_an_unknown_kind_comes_in_as_the_owners_own_jar(core, tmp_path):
    folder = write(tmp_path / "old", {"myfork.jar": SERVER_JAR, "world/level.dat": b"x"})
    with pytest.raises(bringin.BringInError, match="kind it's based on"):
        await bringin.bring_in(core, path=str(folder), name="Fork", directory=str(tmp_path / "n"))
    with pytest.raises(bringin.BringInError, match="trust"):
        await bringin.bring_in(
            core, path=str(folder), name="Fork", directory=str(tmp_path / "n"), based_on="paper"
        )
    job = await bringin.bring_in(
        core,
        path=str(folder),
        name="Fork",
        directory=str(tmp_path / "n"),
        based_on="paper",
        jar="myfork.jar",
        trusted=True,
    )
    assert (await finish(core, job)).state == SUCCEEDED
    settings = core.get_server("fork").config.server
    assert (settings.type, settings.jar, settings.custom_jar) == ("paper", "myfork.jar", True)


async def test_a_curseforge_zip_gets_the_official_installer_and_keeps_its_mods(
    core,
    tmp_path,
    fake_installer,  # noqa: F811
):
    pack = zipped(
        tmp_path / "Pack-Server.zip",
        {
            "variables.txt": "MINECRAFT_VERSION=1.20.1\nMODLOADER=Forge\nMODLOADER_VERSION=47.2.0\n",
            "startserver.bat": "java -jar forge-installer.jar --installServer\n",
            "startserver.sh": "#!/bin/sh\nrm -rf /\n",
            "mods/jei.jar": b"m",
        },
        root="Pack-Server/",
    )
    job = await bringin.bring_in(core, path=str(pack), name="Pack", directory=str(tmp_path / "p"))
    done = await finish(core, job)
    assert done.state == SUCCEEDED, done.message
    ctx = core.get_server("pack")
    assert ctx.config.server.type == "forge" and ctx.config.server.args_file
    assert (tmp_path / "p" / "mods" / "jei.jar").is_file()
    # Only the installer from Forge's own Maven ran, never the pack's scripts.
    assert len(fake_installer) == 1
    argv = fake_installer[0]["argv"]
    assert argv[-1] == "--installServer" and argv[-2] == "forge-installer.jar"
    assert not any("startserver" in str(part) for part in argv)
    assert (tmp_path / "p" / "startserver.sh").read_text().endswith("rm -rf /\n"), "copied, not run"


async def test_a_zip_bigger_than_the_free_disk_is_refused(core, tmp_path, monkeypatch):
    pack = zipped(tmp_path / "big.zip", LAYOUTS["vanilla"])
    monkeypatch.setattr(bringin.shutil, "disk_usage", lambda _path: SimpleNamespace(free=10))
    with pytest.raises(bringin.BringInError, match="free"):
        await bringin.bring_in(core, path=str(pack), name="Big", directory=str(tmp_path / "b"))
    assert not (tmp_path / "b").exists()


def test_bringing_in_is_owner_only_over_the_api(multi_client, tmp_path):
    folder = write(tmp_path / "old", LAYOUTS["vanilla"])
    read = multi_client.post("/api/bring-in/read", json={"path": str(folder)})
    assert read.status_code == 200 and read.json()["type"] == "vanilla"
    started = multi_client.post(
        "/api/bring-in",
        json={"path": str(folder), "name": "Old", "directory": str(tmp_path / "copy")},
    )
    assert started.status_code == 200, started.text
    assert started.json()["job"]["kind"] == "bring_in"
