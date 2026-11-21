"""Importing a Modrinth modpack (.mrpack).

The pack is read and shown before anything happens. Mods come only from
Modrinth's CDN and must match the SHA-512 the pack lists; the overrides are
extracted with the same zip-slip check backup restore uses. Every request
goes to a fake network, so the real safe downloader runs.
"""

from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile

import httpx
import pytest

from agent import downloads, modpack
from agent.core import AgentCore
from agent.modpack import ModpackError
from agent.servertypes import create as create_module

from .conftest import build_multi_config
from .test_server_types import MC, fake_sources

LITHIUM = b"PK\x03\x04 lithium mod bytes" * 40
SODIUM = b"PK\x03\x04 sodium is client only" * 40
CDN = "https://cdn.modrinth.com/data/gvQqBUqZ/versions/abc/lithium.jar"


def mod_entry(path="mods/lithium.jar", content=LITHIUM, url=CDN, sha512=None, server="required"):
    return {
        "path": path,
        "hashes": {
            "sha1": hashlib.sha1(content).hexdigest(),
            **({"sha512": sha512} if sha512 == "" else {}),
            **({"sha512": sha512 or hashlib.sha512(content).hexdigest()} if sha512 != "" else {}),
        },
        "env": {"client": "required", "server": server},
        "downloads": [url],
        "fileSize": len(content),
    }


def make_pack(path, files=None, overrides=None, dependencies=None, symlink=None):
    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": "1.2.0",
        "name": "Test Pack",
        "summary": "A small pack",
        "files": files
        if files is not None
        else [
            mod_entry(),
            mod_entry(
                "mods/sodium.jar",
                SODIUM,
                "https://cdn.modrinth.com/data/x/sodium.jar",
                server="unsupported",
            ),
        ],
        "dependencies": dependencies or {"minecraft": MC, "fabric-loader": "0.16.5"},
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("modrinth.index.json", json.dumps(index))
        for name, data in (
            overrides
            if overrides is not None
            else {
                "overrides/config/lithium.properties": "mixin.ai=false\n",
                "overrides/resourcepacks/pretty.zip": "client only",
                "server-overrides/server-icon.txt": "server",
                "overrides/scripts/run.bat": "echo hi",
            }
        ).items():
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
    return path


@pytest.fixture
def requests_made():
    return []


@pytest.fixture
def network(monkeypatch, requests_made):
    """The fake official sources plus Modrinth's CDN. ``serve`` changes
    what the CDN hands out."""
    served = {"lithium.jar": LITHIUM, "sodium.jar": SODIUM}
    base = fake_sources()

    def handler(request: httpx.Request) -> httpx.Response:
        requests_made.append(str(request.url))
        if request.url.host == "cdn.modrinth.com":
            name = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, content=served[name])
        return base(request)

    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(handler))
    return served


@pytest.fixture
async def core(tmp_path, network, monkeypatch):
    # Whatever Java the machine has is beside the point here (the CI runner
    # has 17, Minecraft 1.21 needs 21): the Java check has its own test.
    monkeypatch.setattr(create_module, "java_problem", lambda *args, **kwargs: None)
    agent = AgentCore(build_multi_config(tmp_path))
    try:
        yield agent
    finally:
        for ctx in list(agent.servers.values()):
            if ctx.server.running:
                await ctx.server.stop()
        await agent.jobs.stop()
        agent.db.close()


def upload(core, pack_path):
    token, path = modpack.new_upload(core)
    path.write_bytes(pack_path.read_bytes())
    return token


# ------------------------------------------------------------ reading
def test_the_pack_is_described_before_anything_happens(tmp_path):
    pack = modpack.inspect(make_pack(tmp_path / "p.mrpack"))
    view = pack.to_dict()
    assert view["minecraft_version"] == MC
    assert view["type"] == "fabric" and view["loader_version"] == "0.16.5"
    assert [f["name"] for f in view["files"]] == ["lithium.jar", "sodium.jar"]
    assert view["download_count"] == 1
    assert view["client_only"] == ["sodium.jar"]
    # Client folders and programs in the overrides are skipped, and said so.
    assert "resourcepacks" in view["skipped_overrides"]
    assert "scripts/run.bat" in view["skipped_overrides"]
    assert view["overrides"] == 2
    assert view["can_import"] is True
    # The hashes and addresses are never sent to the page.
    assert "sha512" not in view["files"][0] and "url" not in view["files"][0]


@pytest.mark.parametrize(
    "entry, reason",
    [
        (mod_entry(sha512=""), "SHA-512"),
        (mod_entry(sha512="abc"), "SHA-512"),
        (mod_entry(url="https://evil.example.com/lithium.jar"), "Modrinth"),
        (mod_entry(url="http://cdn.modrinth.com/data/x/lithium.jar"), "Modrinth"),
        (mod_entry(url="https://cdn.modrinth.com.evil.example/lithium.jar"), "Modrinth"),
        (mod_entry(path="../../evil.jar"), "safe"),
        (mod_entry(path="/etc/evil.jar"), "safe"),
        (mod_entry(path="mods/../../evil.jar"), "safe"),
        (mod_entry(path="C:/Windows/evil.jar"), "safe"),
    ],
)
def test_a_file_that_cant_be_downloaded_safely_blocks_the_pack(tmp_path, entry, reason):
    pack = modpack.inspect(make_pack(tmp_path / "p.mrpack", files=[entry]))
    assert pack.files[0].status == "refused"
    assert reason in pack.files[0].reason
    assert pack.problems and pack.to_dict()["can_import"] is False


@pytest.mark.parametrize(
    "member",
    [
        "overrides/../../evil.txt",
        "overrides/../outside.txt",
        "server-overrides/config/../../../evil.txt",
        "overrides//etc/evil.txt",
        "overrides/C:/evil.txt",
    ],
)
def test_a_zip_slip_path_in_the_overrides_blocks_the_pack(tmp_path, member):
    pack = modpack.inspect(make_pack(tmp_path / "p.mrpack", overrides={member: "boom"}))
    assert any("outside" in p for p in pack.problems)


def test_a_link_in_the_overrides_blocks_the_pack(tmp_path):
    pack = modpack.inspect(
        make_pack(tmp_path / "p.mrpack", overrides={}, symlink="overrides/config/passwd")
    )
    assert any("outside" in p for p in pack.problems)


@pytest.mark.parametrize(
    "content, message",
    [
        (b"not a zip", "isn't a modpack"),
        (None, "modrinth.index.json"),
    ],
)
def test_something_that_isnt_a_modpack_is_refused(tmp_path, content, message):
    path = tmp_path / "p.mrpack"
    if content is None:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("readme.txt", "hi")
    else:
        path.write_bytes(content)
    with pytest.raises(ModpackError, match=message):
        modpack.inspect(path)


def test_a_loader_this_app_doesnt_know_is_refused(tmp_path):
    with pytest.raises(ModpackError):
        modpack.inspect(
            make_pack(tmp_path / "p.mrpack", dependencies={"minecraft": MC, "liteloader": "1"})
        )


def test_a_version_that_isnt_a_version_is_refused(tmp_path):
    with pytest.raises(ModpackError):
        modpack.inspect(make_pack(tmp_path / "p.mrpack", dependencies={"minecraft": "../1.21"}))


# ------------------------------------------------------------ importing
async def test_a_new_server_from_a_pack_has_the_packs_mods_and_files(core, tmp_path, requests_made):
    token = upload(core, make_pack(tmp_path / "p.mrpack"))
    target = tmp_path / "Packed"
    result = await modpack.import_new(
        core, token, name="Packed", directory=str(target), eula_accepted=True
    )
    ctx = core.get_server(result["server_id"])
    assert ctx.config.server.type == "fabric"
    assert (target / "mods" / "lithium.jar").read_bytes() == LITHIUM
    assert (target / "config" / "lithium.properties").read_text() == "mixin.ai=false\n"
    assert (target / "server-icon.txt").is_file()
    # Client-only files were never downloaded or written.
    assert not (target / "mods" / "sodium.jar").exists()
    assert not any("sodium" in url for url in requests_made)
    assert not (target / "resourcepacks").exists() and not (target / "scripts").exists()
    assert result["modpack"]["downloaded"] == 1
    # The upload is gone once used.
    with pytest.raises(ModpackError):
        modpack.upload_path(core, token)


async def test_a_bad_hash_stops_the_import_and_leaves_nothing_behind(core, tmp_path, network):
    # Same size, different bytes: only the SHA-512 can tell.
    network["lithium.jar"] = LITHIUM.replace(b"lithium", b"LITHIUM")
    token = upload(core, make_pack(tmp_path / "p.mrpack"))
    target = tmp_path / "Packed"
    with pytest.raises(Exception, match="(?i)sha-?512|checksum|match"):
        await modpack.import_new(
            core, token, name="Packed", directory=str(target), eula_accepted=True
        )
    assert not target.exists()
    assert sorted(core.servers) == ["creative", "survival"]


async def test_a_pack_with_problems_cannot_be_imported(core, tmp_path):
    token = upload(
        core, make_pack(tmp_path / "p.mrpack", overrides={"overrides/../../evil.txt": "x"})
    )
    with pytest.raises(ModpackError):
        await modpack.import_new(
            core, token, name="Packed", directory=str(tmp_path / "Packed"), eula_accepted=True
        )
    with pytest.raises(ModpackError):
        await modpack.import_into(core.servers["survival"], token)
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "Packed").exists()


async def test_importing_into_a_server_moves_its_mods_aside(core, tmp_path):
    ctx = core.servers["survival"]
    old = ctx.config.mods_dir / "old-mod.jar"
    old.write_bytes(b"old")
    token = upload(core, make_pack(tmp_path / "p.mrpack"))
    plan = modpack.plan_into(ctx, modpack.inspect(modpack.upload_path(core, token)))
    # Its version isn't known from the console, so the pack's is installed first.
    assert plan["version_change"] is True
    result = await modpack.import_into(ctx, token)
    assert (ctx.config.mods_dir / "lithium.jar").read_bytes() == LITHIUM
    assert not old.exists()
    kept = result["moved_aside"]
    assert kept["moved"] == ["old-mod.jar"]
    kept_copy = next(ctx.config.mod_trash_dir.glob("*-before-modpack/old-mod.jar"))
    assert kept_copy.read_bytes() == b"old"
    assert result["undo"]


async def test_a_bad_hash_importing_into_a_server_puts_it_back(core, tmp_path, network):
    ctx = core.servers["survival"]
    old = ctx.config.mods_dir / "old-mod.jar"
    old.write_bytes(b"old")
    (ctx.config.server_dir / "config" / "lithium.properties").write_text("mine\n")
    network["lithium.jar"] = LITHIUM.replace(b"lithium", b"LITHIUM")
    token = upload(core, make_pack(tmp_path / "p.mrpack"))
    with pytest.raises(Exception, match="(?i)sha-?512|checksum|match"):
        await modpack.import_into(ctx, token)
    assert old.read_bytes() == b"old"
    assert not (ctx.config.mods_dir / "lithium.jar").exists()
    assert (ctx.config.server_dir / "config" / "lithium.properties").read_text() == "mine\n"


# ------------------------------------------------------------ the API
def test_inspect_through_the_api(multi_client, tmp_path):
    data = make_pack(tmp_path / "p.mrpack").read_bytes()
    response = multi_client.post(
        "/api/modpacks/inspect?server_id=survival",
        files={"file": ("p.mrpack", io.BytesIO(data), "application/zip")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pack"]["name"] == "Test Pack" and body["into"]["version_change"] is True
    assert multi_client.delete(f"/api/modpacks/{body['token']}").status_code == 200


def test_the_api_refuses_a_file_that_isnt_a_pack(multi_client):
    response = multi_client.post(
        "/api/modpacks/inspect",
        files={"file": ("p.zip", io.BytesIO(b"x"), "application/zip")},
    )
    assert response.status_code == 400
    response = multi_client.post(
        "/api/modpacks/inspect",
        files={"file": ("p.mrpack", io.BytesIO(b"x"), "application/zip")},
    )
    assert response.status_code == 400


def test_importing_into_a_server_needs_confirming(multi_client):
    response = multi_client.post(
        "/api/servers/survival/modpack", json={"token": "0" * 32, "confirm": False}
    )
    assert response.status_code == 400


async def test_a_failed_import_undoes_every_folder_the_pack_wrote(core, tmp_path, monkeypatch):
    """Overrides often write folders the usual backup list leaves out
    (kubejs/, scripts/). A failed import puts those back too, and takes
    away what it added, even into a mods folder that was empty."""
    ctx = core.servers["survival"]
    base = ctx.config.server_dir
    for jar in ctx.config.mods_dir.glob("*"):
        jar.unlink()
    (base / "kubejs").mkdir()
    (base / "kubejs" / "startup.js").write_text("mine\n")
    pack = make_pack(
        tmp_path / "p.mrpack",
        overrides={
            "overrides/kubejs/startup.js": "the pack's\n",
            "overrides/brand-new/extra.txt": "new\n",
        },
    )
    token = upload(core, pack)
    monkeypatch.setattr(modpack, "check_in_place", lambda *a: (False, "a file differs"))
    with pytest.raises(Exception, match="a file differs"):
        await modpack.import_into(ctx, token)
    assert (base / "kubejs" / "startup.js").read_text() == "mine\n"
    assert not (base / "brand-new").exists()
    assert not (ctx.config.mods_dir / "lithium.jar").exists()


async def test_a_packs_jars_are_shared_and_not_downloaded_twice(core, tmp_path, requests_made):
    """A pack's mods go through the shared store: the second server that
    uses the same pack links the stored file instead of downloading it."""
    import os

    first = await modpack.import_new(
        core,
        upload(core, make_pack(tmp_path / "p.mrpack")),
        name="First",
        directory=str(tmp_path / "First"),
        eula_accepted=True,
    )
    downloads_before = sum("cdn.modrinth.com" in url for url in requests_made)
    await modpack.import_new(
        core,
        upload(core, make_pack(tmp_path / "p.mrpack")),
        name="Second",
        directory=str(tmp_path / "Second"),
        eula_accepted=True,
    )
    assert sum("cdn.modrinth.com" in url for url in requests_made) == downloads_before
    one, two = (
        tmp_path / "First" / "mods" / "lithium.jar",
        tmp_path / "Second" / "mods" / "lithium.jar",
    )
    assert two.read_bytes() == LITHIUM
    assert os.path.samefile(one, two)
    assert core.get_server(first["server_id"]).mods.store.usage()["files"] == 1


# ------------------------------------------------------------ picked on Modrinth
def modrinth_versions(monkeypatch, files):
    """Modrinth's API answering one modpack project ``xyz`` whose newest
    release holds ``files``."""
    from agent.mods.modrinth import ModrinthClient

    asked = []

    async def send(self, method, path, **sent):
        asked.append(path)
        assert path == "/project/xyz/version" and "loaders" not in sent["params"]
        return [{"id": "v2", "project_id": "xyz", "version_type": "release", "files": files}]

    monkeypatch.setattr(ModrinthClient, "_send", send)
    return asked


def pack_file(data, url="https://cdn.modrinth.com/data/xyz/versions/v2/test-pack.mrpack"):
    return {
        "url": url,
        "filename": "test-pack.mrpack",
        "primary": True,
        "size": len(data),
        "hashes": {"sha512": hashlib.sha512(data).hexdigest()},
    }


def uploads_left(multi_client):
    return list(modpack.upload_folder(multi_client.app.state.core).glob("*.mrpack*"))


def test_a_modrinth_pack_picked_by_name_is_read_like_a_file(
    multi_client, network, monkeypatch, tmp_path
):
    data = make_pack(tmp_path / "p.mrpack").read_bytes()
    network["test-pack.mrpack"] = data
    modrinth_versions(monkeypatch, [pack_file(data)])
    read = multi_client.post(
        "/api/modpacks/modrinth", json={"project": "xyz", "server_id": "survival"}
    )
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["pack"]["name"] == "Test Pack" and body["into"]["version_change"] is True
    assert multi_client.delete(f"/api/modpacks/{body['token']}").status_code == 200
    assert not uploads_left(multi_client)


@pytest.mark.parametrize(
    ("served", "url", "says"),
    [
        (True, None, "checksum"),
        (False, "https://example.com/test-pack.mrpack", "can't be checked"),
    ],
)
def test_a_modrinth_pack_that_cant_be_checked_is_thrown_away(
    multi_client, network, monkeypatch, tmp_path, served, url, says
):
    data = make_pack(tmp_path / "p.mrpack").read_bytes()
    network["test-pack.mrpack"] = data[:-1] + b"?" if served else data  # same size, other bytes
    modrinth_versions(monkeypatch, [pack_file(data, *([url] if url else []))])
    refused = multi_client.post("/api/modpacks/modrinth", json={"project": "xyz"})
    assert refused.status_code == 400 and says in refused.json()["detail"]
    assert not uploads_left(multi_client)


def test_a_project_name_that_could_change_the_path_is_refused(multi_client):
    refused = multi_client.post("/api/modpacks/modrinth", json={"project": "../user"})
    assert refused.status_code == 422
