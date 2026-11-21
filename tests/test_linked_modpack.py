# ruff: noqa: F811  (the fixtures imported from test_modpack are used by name)
"""A linked modpack and "Modpack update" (agent/linkedpack.py).

A server made from a modpack remembers it. Modpack update compares files by
hash, keeps the mods and config files the owner added or changed, runs as
one change with one backup and one Undo, and a failure puts everything
back. Modrinth is a fake behind the client; its CDN is the fake network of
test_modpack, so the real safe downloader runs.
"""

from __future__ import annotations

import hashlib

import pytest

from agent import linkedpack, modpack
from agent.modpack import ModpackError
from agent.mods.modrinth import ModrinthClient

from .test_modpack import (  # noqa: F401
    LITHIUM,
    core,
    make_pack,
    mod_entry,
    network,
    requests_made,
    upload,
)

LITHIUM2 = b"PK\x03\x04 lithium two" * 40
FERRITE = b"PK\x03\x04 ferrite core" * 40
CDN = "https://cdn.modrinth.com/data"
NEW_CONFIG = "mixin.ai=true\n"


def v1_pack(tmp_path):
    return make_pack(
        tmp_path / "v1.mrpack",
        overrides={"overrides/config/lithium.properties": "mixin.ai=false\n"},
    )


def v2_pack(tmp_path):
    path = make_pack(
        tmp_path / "v2.mrpack",
        files=[
            # The same mod (project gvQqBUqZ) in a newer file, and one new mod.
            mod_entry("mods/lithium-2.jar", LITHIUM2, f"{CDN}/gvQqBUqZ/versions/def/lithium-2.jar"),
            mod_entry("mods/ferrite.jar", FERRITE, f"{CDN}/uXXizFIs/versions/f1/ferrite.jar"),
        ],
        overrides={"overrides/config/lithium.properties": NEW_CONFIG},
    )
    return path


class FakeModrinth:
    """Modrinth knowing one modpack project, ``xyz``, with its versions
    (newest first), each one .mrpack file served by the fake CDN."""

    def __init__(self, monkeypatch, network):
        self.versions: list[dict] = []
        self.network = network
        fake = self

        async def send(client, method, path, **sent):
            if path == "/project/xyz/version":
                return fake.versions
            if path.startswith("/version/"):
                return next(v for v in fake.versions if v["id"] == path.rsplit("/", 1)[-1])
            if path.startswith("/version_file/"):
                sha = path.rsplit("/", 1)[-1]
                for v in fake.versions:
                    if v["files"][0]["hashes"]["sha512"] == sha:
                        return v
                from agent.mods.modrinth import ModrinthNotFound

                raise ModrinthNotFound("not found")
            raise AssertionError(path)

        monkeypatch.setattr(ModrinthClient, "_send", send)

    def publish(self, vid, number, pack_path, date):
        data = pack_path.read_bytes()
        name = f"pack-{vid}.mrpack"
        self.network[name] = data
        self.versions.insert(
            0,
            {
                "id": vid,
                "project_id": "xyz",
                "version_number": number,
                "version_type": "release",
                "date_published": date,
                "files": [
                    {
                        "url": f"{CDN}/xyz/versions/{vid}/{name}",
                        "filename": name,
                        "primary": True,
                        "size": len(data),
                        "hashes": {"sha512": hashlib.sha512(data).hexdigest()},
                    }
                ],
            },
        )


@pytest.fixture
def site(monkeypatch, network):
    network["lithium-2.jar"] = LITHIUM2
    network["ferrite.jar"] = FERRITE
    return FakeModrinth(monkeypatch, network)


async def linked_server(core, tmp_path, site):
    """A new server made from version 1.2.0 of ``xyz``, picked on Modrinth."""
    site.publish("v1", "1.2.0", v1_pack(tmp_path), "2026-01-01T00:00:00Z")
    token, _pack = await modpack.from_modrinth(core, "xyz")
    result = await modpack.import_new(
        core, token, name="Packed", directory=str(tmp_path / "Packed"), eula_accepted=True
    )
    return core.get_server(result["server_id"])


# ------------------------------------------------------------ the link
async def test_a_pack_picked_on_modrinth_links_the_server(core, tmp_path, site):
    ctx = await linked_server(core, tmp_path, site)
    link = linkedpack.read_link(ctx)
    assert (link["site"], link["project"], link["version_id"], link["version"]) == (
        "modrinth",
        "xyz",
        "v1",
        "1.2.0",
    )
    [entry] = link["files"]
    assert entry["path"] == "mods/lithium.jar" and entry["project"] == "gvQqBUqZ"
    assert entry["sha256"] == hashlib.sha256(LITHIUM).hexdigest()
    assert "config/lithium.properties" in link["overrides"]


async def test_an_uploaded_pack_is_linked_by_its_hash(core, tmp_path, site):
    site.publish("v1", "1.2.0", v1_pack(tmp_path), "2026-01-01T00:00:00Z")
    token = upload(core, tmp_path / "v1.mrpack")
    result = await modpack.import_new(
        core, token, name="Up", directory=str(tmp_path / "Up"), eula_accepted=True
    )
    assert linkedpack.read_link(core.get_server(result["server_id"]))["version_id"] == "v1"


async def test_a_pack_no_site_knows_is_imported_but_not_linked(core, tmp_path, site):
    token = upload(core, v1_pack(tmp_path))
    result = await modpack.import_new(
        core, token, name="Own", directory=str(tmp_path / "Own"), eula_accepted=True
    )
    ctx = core.get_server(result["server_id"])
    assert (ctx.config.mods_dir / "lithium.jar").is_file()
    assert linkedpack.read_link(ctx) is None


@pytest.mark.parametrize(
    ("was", "now"),
    [('"modrinth"', '"example.com"'), ('"mods/lithium.jar"', '"../lithium.jar"')],
)
async def test_a_link_file_that_was_tampered_with_counts_as_not_linked(
    core, tmp_path, site, was, now
):
    ctx = await linked_server(core, tmp_path, site)
    path = ctx.config.server_dir / linkedpack.LINK_FILE
    assert was in path.read_text()
    path.write_text(path.read_text().replace(was, now))
    assert linkedpack.read_link(ctx) is None


# ------------------------------------------------------------ the update
async def test_modpack_update_keeps_what_you_added_and_undo_puts_it_back(
    core,
    tmp_path,
    site,
):
    ctx = await linked_server(core, tmp_path, site)
    mods, config = ctx.config.mods_dir, ctx.config.server_dir / "config" / "lithium.properties"
    (mods / "mine.jar").write_bytes(b"PK\x03\x04 my own mod")
    config.write_text("mine=true\n")
    site.publish("v2", "1.3.0", v2_pack(tmp_path), "2026-02-01T00:00:00Z")

    await linkedpack.check(ctx)
    card = linkedpack.card(ctx, ctx.mods.list_installed())
    assert card["update"] == {"version_id": "v2", "version": "1.3.0"}
    assert card["added_by_you"] == 1

    preview = await linkedpack.preview(ctx)
    changes = preview["changes"]
    assert changes["newer"] == [
        {"name": "lithium-2.jar", "from": "lithium.jar", "to": "lithium-2.jar"}
    ]
    assert changes["added"] == ["ferrite.jar"] and changes["removed"] == []
    assert changes["added_by_you"] == ["mine.jar"]
    assert changes["configs_changed_by_you"] == ["config/lithium.properties"]

    result = await linkedpack.update(ctx, preview["token"])
    assert not (mods / "lithium.jar").exists()
    assert (mods / "lithium-2.jar").read_bytes() == LITHIUM2
    assert (mods / "ferrite.jar").read_bytes() == FERRITE
    assert (mods / "mine.jar").is_file()
    assert config.read_text() == "mine=true\n"
    assert result["kept_configs"] == ["config/lithium.properties"] and result["undo"]
    assert linkedpack.read_link(ctx)["version_id"] == "v2"
    assert linkedpack.card(ctx, ctx.mods.list_installed())["update"] is None

    await ctx.backups.changes.undo("admin")
    assert (mods / "lithium.jar").read_bytes() == LITHIUM
    assert not (mods / "lithium-2.jar").exists() and not (mods / "ferrite.jar").exists()
    assert (mods / "mine.jar").is_file()
    assert linkedpack.read_link(ctx)["version_id"] == "v1"


async def test_use_the_modpacks_config_files(core, tmp_path, site):
    ctx = await linked_server(core, tmp_path, site)
    config = ctx.config.server_dir / "config" / "lithium.properties"
    config.write_text("mine=true\n")
    site.publish("v2", "1.3.0", v2_pack(tmp_path), "2026-02-01T00:00:00Z")
    preview = await linkedpack.preview(ctx)
    await linkedpack.update(ctx, preview["token"], use_pack_configs=True)
    assert config.read_text() == NEW_CONFIG


async def test_a_turned_off_pack_mod_stays_off(core, tmp_path, site):
    ctx = await linked_server(core, tmp_path, site)
    mods = ctx.config.mods_dir
    v3 = make_pack(
        tmp_path / "v3.mrpack",
        files=[
            mod_entry(),
            mod_entry("mods/ferrite.jar", FERRITE, f"{CDN}/uXXizFIs/versions/f1/ferrite.jar"),
        ],
        overrides={},
    )
    (mods / "lithium.jar").rename(mods / "lithium.jar.disabled")
    site.publish("v3", "1.4.0", v3, "2026-03-01T00:00:00Z")
    await linkedpack.update(ctx, (await linkedpack.preview(ctx))["token"])
    assert not (mods / "lithium.jar").exists() and (mods / "lithium.jar.disabled").is_file()
    assert (mods / "ferrite.jar").is_file()


async def test_a_failed_download_puts_everything_back(core, tmp_path, site, network):
    ctx = await linked_server(core, tmp_path, site)
    mods = ctx.config.mods_dir
    site.publish("v2", "1.3.0", v2_pack(tmp_path), "2026-02-01T00:00:00Z")
    network["ferrite.jar"] = FERRITE.replace(b"ferrite", b"FERRITE")  # same size, other bytes
    preview = await linkedpack.preview(ctx)
    with pytest.raises(Exception, match="(?i)sha-?512|checksum|match"):
        await linkedpack.update(ctx, preview["token"])
    assert (mods / "lithium.jar").read_bytes() == LITHIUM
    assert not (mods / "lithium-2.jar").exists() and not (mods / "ferrite.jar").exists()
    assert linkedpack.read_link(ctx)["version_id"] == "v1"


async def test_another_packs_download_cant_update_this_server(core, tmp_path, site):
    ctx = await linked_server(core, tmp_path, site)
    token = upload(core, v2_pack(tmp_path))  # a file, not from this pack's site
    with pytest.raises(ModpackError, match="isn't this server's modpack"):
        await linkedpack.update(ctx, token)


async def test_an_older_release_is_not_offered_as_an_update(core, tmp_path, site):
    ctx = await linked_server(core, tmp_path, site)
    site.publish("v0", "1.1.0", v2_pack(tmp_path), "2025-06-01T00:00:00Z")
    await linkedpack.check(ctx)
    assert linkedpack.card(ctx, [])["update"] is None


async def test_the_background_check_leaves_curseforge_out(core, tmp_path, site, monkeypatch):
    ctx = await linked_server(core, tmp_path, site)
    link = linkedpack.read_link(ctx)
    linkedpack._save(ctx, {**link, "site": "curseforge", "project": "curseforge:1"})
    asked = []

    async def newest(core, link):
        asked.append(link["project"])

    monkeypatch.setattr(linkedpack, "_newest", newest)
    await linkedpack.check(ctx, relay=False)
    assert asked == []
    await linkedpack.check(ctx, relay=True)
    assert asked == ["curseforge:1"]


# ------------------------------------------------------------ through the API
def test_unlink_through_the_api(multi_client, tmp_path):
    core = multi_client.app.state.core
    ctx = core.servers["survival"]
    (ctx.config.mods_dir / "a.jar").write_bytes(b"PK\x03\x04 a")
    link = {
        "site": "modrinth",
        "project": "xyz",
        "version_id": "v1",
        "version": "1.2.0",
        "name": "Test Pack",
        "files": [],
        "overrides": {},
        "published": "2099-01-01T00:00:00Z",
    }
    linkedpack._save(ctx, link)
    ctx.db.set_setting(
        linkedpack.UPDATE_SETTING, {"for": "v1", "checked_at": 9e12}
    )  # checked "just now": no network
    body = multi_client.get("/api/servers/survival/mods").json()
    assert body["modpack"]["name"] == "Test Pack" and body["modpack"]["site"] == "Modrinth"
    assert next(m for m in body["mods"] if m["filename"] == "a.jar")["added_by_you"] is True
    assert multi_client.delete("/api/servers/survival/modpack/link").status_code == 200
    body = multi_client.get("/api/servers/survival/mods").json()
    assert body["modpack"] is None
    assert any(m["filename"] == "a.jar" for m in body["mods"])
    assert multi_client.delete("/api/servers/survival/modpack/link").status_code == 404


def test_modpack_update_needs_confirming(multi_client):
    refused = multi_client.post("/api/servers/survival/modpack/update", json={"token": "0" * 32})
    assert refused.status_code == 400 and "confirmed" in refused.json()["detail"]
