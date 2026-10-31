"""Hangar next to Modrinth, and add-ons added from a file or a link, driven
through the agent API like the apps do. Hangar and GitHub are fakes behind
the safe downloader's swappable transport; Modrinth is the fake from
test_dependency_resolution. Nothing leaves the machine."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from agent import downloads
from agent.mods import github

from .conftest import PASSWORD, build_config
from .test_api import auth, token_for
from .test_dependency_resolution import FakeModrinth
from .test_mods import make_jar

MC = "1.21.4"


def zip_of(files: dict[str, str | bytes]) -> bytes:
    """The same bytes every time (a fixed date inside), so hashes match."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in files.items():
            zf.writestr(zipfile.ZipInfo(name, (2026, 10, 30, 0, 0, 0)), text)
    return buffer.getvalue()


def plugin_jar(name: str, version: str = "1.0") -> bytes:
    lines = [f"name: {name}", f"version: '{version}'", "main: x.Main", "api-version: '1.21'"]
    return zip_of({"plugin.yml": "\n".join(lines), "x/Main.class": b"\xca\xfe\xba\xbe"})


DATAPACK = zip_of(
    {
        "pack.mcmeta": json.dumps({"pack": {"pack_format": 61, "description": "Trees"}}),
        "data/trees/function/grow.mcfunction": "say hi",
    }
)


class FakeSites:
    """Hangar and GitHub as the downloader sees them. ``plugins``: Hangar
    project id -> (name, [(version id, version, channel, deps)])."""

    def __init__(self) -> None:
        self.down: set[str] = set()
        self.asked: list[str] = []
        self.plugins: dict[int, tuple[str, list[tuple]]] = {
            31: ("ViaVersion", [(301, "5.2", "Release", []), (300, "5.1", "Release", [])]),
            40: (
                "Shops",
                [
                    (
                        400,
                        "2.0",
                        "Release",
                        [
                            {"name": "Vault", "projectId": 50, "required": True},
                            {"name": "Holo", "projectId": 60, "required": False},
                            {
                                "name": "Citizens",
                                "projectId": None,
                                "required": True,
                                "externalUrl": "https://example.com/citizens",
                            },
                        ],
                    )
                ],
            ),
            50: ("Vault", [(501, "1.8-SNAPSHOT", "Snapshot", []), (500, "1.7", "Release", [])]),
            60: ("Holo", [(600, "3.0", "Release", [])]),
        }
        self.bad_hash: set[int] = set()
        self.releases = {
            "v1.2": [("TreePlugin-1.2.jar", 11), ("TreePlugin-1.2-sources.jar", 12)],
            "v1.3": [("TreePlugin-1.3.jar", 21)],
        }
        self.latest = "v1.2"

    def jar(self, version_id: int) -> bytes:
        for name, versions in self.plugins.values():
            for vid, number, *_ in versions:
                if vid == version_id:
                    return plugin_jar(name, number)
        raise KeyError(version_id)

    def asset_bytes(self, name: str) -> bytes:
        return plugin_jar("TreePlugin", name.split("-")[1].removesuffix(".jar"))

    def version(self, pid: int, entry: tuple) -> dict:
        vid, number, channel, deps = entry
        name = self.plugins[pid][0]
        data = self.jar(vid)
        digest = "0" * 64 if vid in self.bad_hash else hashlib.sha256(data).hexdigest()
        return {
            "id": vid,
            "projectId": pid,
            "name": number,
            "visibility": "public",
            "createdAt": f"2026-10-{vid % 28 + 1:02d}T00:00:00Z",
            "channel": {"name": channel},
            "stats": {"totalDownloads": 5},
            "downloads": {
                "PAPER": {
                    "fileInfo": {
                        "name": f"{name}-{number}.jar",
                        "sizeBytes": len(data),
                        "sha256Hash": digest,
                    },
                    "downloadUrl": f"https://hangarcdn.papermc.io/plugins/o/{name}/versions/{number}/PAPER/{name}-{number}.jar",
                    "externalUrl": None,
                }
            },
            "pluginDependencies": {"PAPER": [{"platform": "PAPER", **d} for d in deps]},
            "platformDependencies": {"PAPER": [MC]},
        }

    def project(self, pid: int) -> dict:
        name = self.plugins[pid][0]
        return {
            "id": pid,
            "name": name,
            "namespace": {"owner": "someone", "slug": name},
            "stats": {"downloads": 1000 + pid},
            "description": f"{name} for Paper",
            "visibility": "public",
            "supportedPlatforms": {"PAPER": [MC]},
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.asked.append(str(url))
        if url.host in self.down:
            raise httpx.ConnectError("down", request=request)
        path, query = url.path, parse_qs(urlparse(str(url)).query)
        if url.host == "hangar.papermc.io":
            parts = path.removeprefix("/api/v1/").split("/")
            if parts == ["projects"]:
                assert query["platform"] == ["PAPER"]
                found = [
                    self.project(pid)
                    for pid, (name, _) in self.plugins.items()
                    if query["query"][0].lower() in name.lower()
                ]
                found.append({**self.project(31), "id": 99, "visibility": "needsApproval"})
                return httpx.Response(
                    200, json={"pagination": {"count": len(found)}, "result": found}
                )
            if parts[0] == "projects" and len(parts) == 2:
                return httpx.Response(200, json=self.project(int(parts[1])))
            if parts[0] == "projects" and parts[2] == "versions":
                pid = int(parts[1])
                entries = [
                    self.version(pid, e)
                    for e in self.plugins[pid][1]
                    if "channel" not in query or e[2] == query["channel"][0]
                ]
                return httpx.Response(200, json={"pagination": {}, "result": entries})
            if parts[:2] == ["versions", "hash"]:
                for pid, (_, versions) in self.plugins.items():
                    for entry in versions:
                        if hashlib.sha256(self.jar(entry[0])).hexdigest() == parts[2]:
                            return httpx.Response(200, json=self.project(pid))
                return httpx.Response(404, json={"title": "Not Found"})
            if parts[0] == "versions":
                vid = int(parts[1])
                for pid, (_, versions) in self.plugins.items():
                    for entry in versions:
                        if entry[0] == vid:
                            return httpx.Response(200, json=self.version(pid, entry))
                return httpx.Response(404)
        if url.host == "hangarcdn.papermc.io":
            name, number = path.split("/")[3], path.split("/")[5]
            for n, versions in self.plugins.values():
                for entry in versions:
                    if n == name and entry[1] == number:
                        return httpx.Response(200, content=self.jar(entry[0]))
        if url.host == "api.github.com" and path.startswith("/repos/tree-maker/trees/releases"):
            rest = path.removeprefix("/repos/tree-maker/trees/releases")
            if rest.startswith("/assets/"):
                wanted = int(rest.removeprefix("/assets/"))
                for tag, files in self.releases.items():
                    for name, asset_id in files:
                        if asset_id == wanted:
                            return httpx.Response(200, json=self.asset(tag, name, asset_id))
                return httpx.Response(404)
            tag = self.latest if rest == "/latest" else rest.removeprefix("/tags/")
            if tag not in self.releases:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "tag_name": tag,
                    "name": f"Trees {tag}",
                    "html_url": f"https://github.com/tree-maker/trees/releases/tag/{tag}",
                    "assets": [self.asset(tag, n, i) for n, i in self.releases[tag]]
                    + [{"id": 99, "name": "notes.txt", "size": 3}],
                },
            )
        if url.host == "github.com" and "/releases/download/" in path:
            return httpx.Response(
                302, headers={"location": f"https://release-assets.githubusercontent.com/x{path}"}
            )
        if url.host == "release-assets.githubusercontent.com":
            return httpx.Response(200, content=self.asset_bytes(path.rsplit("/", 1)[1]))
        if url.host == "identitytoolkit.googleapis.com":
            return httpx.Response(503)  # CurseForge (test_curseforge.py) is down here
        raise AssertionError(f"unexpected request to {url}")

    def asset(self, tag: str, name: str, asset_id: int) -> dict:
        data = self.asset_bytes(name)
        return {
            "id": asset_id,
            "name": name,
            "size": len(data),
            "digest": f"sha256:{hashlib.sha256(data).hexdigest()}" if asset_id != 21 else None,
            "browser_download_url": f"https://github.com/tree-maker/trees/releases/download/{tag}/{name}",
        }


class PluginModrinth(FakeModrinth):
    """Modrinth with one plugin, EssentialsX, for Paper."""

    def __init__(self, known_hashes=()):
        super().__init__(
            {
                "essentials": {
                    "project_id": "ESSX0001",
                    "title": "EssentialsX",
                    "versions": [("2.21", [])],
                }
            },
            mc=MC,
        )
        self.known = set(known_hashes)

    def _version(self, slug, number, deps, filename=None):
        return {**super()._version(slug, number, deps, filename), "loaders": ["paper"]}

    async def by_hash(self, sha512):
        return "ESSX0001" if sha512 in self.known else None

    async def download(self, version):
        data = plugin_jar("essentials", version["version_number"])
        return data, version["file"]["filename"], hashlib.sha256(data).hexdigest()


@pytest.fixture
def sites(monkeypatch):
    fake = FakeSites()
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(fake))
    return fake


def app_for(tmp_path, monkeypatch, server_type: str):
    from fastapi.testclient import TestClient

    from agent.main import create_app
    from agent.security.auth import hash_password

    monkeypatch.setenv("MCSC_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MCSC_ADMIN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))
    monkeypatch.delenv("MCSC_API_TOKEN", raising=False)
    config = build_config(tmp_path, **{"server.type": server_type})
    return TestClient(create_app(config))


@pytest.fixture
def paper(tmp_path, monkeypatch, sites):
    """A stopped Paper server on MC that has started once, its token and its plugins folder."""
    with app_for(tmp_path, monkeypatch, "paper") as client:
        core = client.app.state.core
        core.mods.server.mc_version = MC
        core.mods.modrinth = PluginModrinth()
        folder = core.mods.mods_dir
        yield client, auth(token_for(client)), folder


def plugin_names(client, headers):
    return {m["mod_id"]: m for m in client.get("/api/mods", headers=headers).json()["mods"]}


# ---------------------------------------------------------------- search
def test_search_shows_both_sites_named_and_only_public_hangar_plugins(paper, sites):
    client, headers, _ = paper
    found = client.get("/api/mods/search?q=via&limit=10", headers=headers).json()
    hits = found["hits"]
    assert [h["source_name"] for h in hits] == ["Hangar"]  # FakeModrinth has no "via"
    assert hits[0]["title"] == "ViaVersion" and hits[0]["project_id"] == "hangar:31"
    assert hits[0]["downloads"] == 1031  # Hangar's own count, not a guess
    assert "hangar:99" not in {h["project_id"] for h in hits}, "a project awaiting review"
    asked = next(u for u in sites.asked if "/projects?" in u)
    assert f"version={MC}" in asked and "platform=PAPER" in asked

    both = client.get("/api/mods/search?q=s&limit=10", headers=headers).json()
    assert {h["source_name"] for h in both["hits"]} == {"Modrinth", "Hangar"}
    assert both["problems"] == []


def test_hangar_down_never_blocks_modrinth(paper, sites):
    client, headers, _ = paper
    sites.down.add("hangar.papermc.io")
    found = client.get("/api/mods/search?q=essentials", headers=headers).json()
    assert [h["title"] for h in found["hits"]] == ["EssentialsX"]
    assert found["problems"] == ["Hangar isn't available right now."]


def test_hangar_is_not_offered_on_a_fabric_server(client, monkeypatch):
    def nothing_from_hangar(request):
        assert "hangar" not in request.url.host, f"asked {request.url}"
        return httpx.Response(503)  # CurseForge, asked on mod servers instead

    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(nothing_from_hangar))
    client.app.state.core.mods.modrinth = FakeModrinth({})
    headers = auth(token_for(client))
    assert client.get("/api/mods/search?q=via", headers=headers).json()["hits"] == []
    refused = client.get("/api/mods/project/hangar:31", headers=headers)
    assert refused.status_code == 400 and "Paper" in refused.json()["detail"]


# ---------------------------------------------------------------- install
def test_installing_from_hangar_adds_required_plugins_and_shows_the_rest(paper, sites):
    client, headers, folder = paper
    project = client.get("/api/mods/project/hangar:40", headers=headers).json()
    assert project["project"]["title"] == "Shops"
    result = client.post(
        "/api/mods/install",
        json={"project": "hangar:40", "install_dependencies": True},
        headers=headers,
    ).json()
    assert result["installed"]["mod_id"] == "Shops"
    # Vault is required and on Hangar: added, from its Release channel (not the snapshot).
    assert [d["title"] for d in result["dependencies_installed"]] == ["Vault"]
    assert (folder / "Vault-1.7.jar").is_file() and not (folder / "Vault-1.8-SNAPSHOT.jar").exists()
    # Citizens isn't on Hangar: a link to click, never downloaded.
    assert result["dependencies_links"] == [
        {"name": "Citizens", "url": "https://example.com/citizens", "required": True}
    ]
    assert not any("example.com" in u for u in sites.asked)
    # Holo is optional: listed, not added.
    assert [d["name"] for d in result["dependencies_optional"]] == ["Holo"]
    assert not (folder / "Holo-3.0.jar").exists()
    rows = plugin_names(client, headers)
    assert rows["Shops"]["source_name"] == "Hangar" and rows["Shops"]["checked_by"] == "Hangar"


def test_a_hangar_file_that_does_not_match_its_sha256_never_lands(paper, sites):
    client, headers, folder = paper
    sites.bad_hash.add(301)
    response = client.post("/api/mods/install", json={"project": "hangar:31"}, headers=headers)
    assert response.status_code in (400, 502)
    assert "checksum" in response.json()["detail"]
    assert not list(folder.glob("*.jar"))


def test_the_same_file_from_the_other_site_is_not_installed_twice(paper, sites):
    client, headers, folder = paper
    (folder / "ViaVersion.jar").write_bytes(sites.jar(301))
    response = client.post("/api/mods/install", json={"project": "hangar:31"}, headers=headers)
    assert response.status_code == 400
    assert "already installed as ViaVersion.jar" in response.json()["detail"]


# ---------------------------------------------------------------- updates
def test_update_all_covers_both_sites_with_one_backup_and_one_undo(paper, sites):
    client, headers, folder = paper
    (folder / "ViaVersion-5.1.jar").write_bytes(sites.jar(300))  # by hand: matched by its hash
    (folder / "Vault-1.6.jar").write_bytes(plugin_jar("Vault", "1.6"))  # not on Hangar by hash
    core = client.app.state.core
    core.mods.db.set_setting("mod_source:Vault", {"source": "hangar", "project": "hangar:50"})
    (folder / "Essentials.jar").write_bytes(plugin_jar("essentials", "2.20"))
    plan = client.get("/api/mods/update-all", headers=headers).json()
    rows = {r["mod_id"]: r for r in plan["updates"]}
    assert (
        rows["ViaVersion"]["latest_version"] == "5.2" and rows["ViaVersion"]["source"] == "hangar"
    )
    # The snapshot is newer, but only Release builds are offered.
    assert rows["Vault"]["latest_version"] == "1.7"
    assert rows["essentials"]["source"] == "modrinth"

    done = client.post(
        "/api/mods/update-all",
        json={
            "mods": [
                {"filename": r["filename"], "version_id": r["version_id"]} for r in rows.values()
            ]
        },
        headers=headers,
    ).json()
    assert {u["name"] for u in done["updated"]} == {"ViaVersion", "Vault", "essentials"}
    assert (folder / "ViaVersion-5.2.jar").is_file() and (folder / "Vault-1.7.jar").is_file()
    assert done["undo"]
    undo(client, headers)
    assert (folder / "ViaVersion-5.1.jar").is_file() and not (
        folder / "ViaVersion-5.2.jar"
    ).exists()


def undo(client, headers):
    moved = client.post("/api/servers/test/changes/undo", json={}, headers=headers)
    assert moved.status_code == 200, moved.text


# ---------------------------------------------------------------- add from a file
def upload(client, headers, name, data, confirm=True):
    """``confirm``: the person said yes to "no site lists this file"."""
    return client.post(
        f"/api/mods/upload?confirm={str(confirm).lower()}",
        files={"file": (name, data, "application/octet-stream")},
        headers=headers,
    )


def test_a_dropped_file_of_the_wrong_kind_is_refused_with_the_reason(paper, tmp_path):
    client, headers, folder = paper
    fabric = make_jar(tmp_path / "sodium.jar", "sodium", "0.6")
    refused = upload(client, headers, "sodium.jar", fabric.read_bytes())
    assert refused.status_code == 400 and "Fabric" in refused.json()["detail"]
    pack = zip_of({"pack.mcmeta": '{"pack": {}}', "assets/minecraft/x.png": "x"})
    refused = upload(client, headers, "look.zip", pack)
    assert "resource pack" in refused.json()["detail"]
    nested = zip_of({"Trees/pack.mcmeta": '{"pack": {}}', "Trees/data/x": "x"})
    assert "folder inside the zip" in upload(client, headers, "t.zip", nested).json()["detail"]
    assert not list(folder.glob("*"))


def test_a_datapack_goes_into_the_world_with_a_backup_and_undo(paper):
    client, headers, folder = paper
    added = upload(client, headers, "trees.zip", DATAPACK).json()
    target = client.app.state.core.config.server_dir / "world" / "datapacks" / "trees.zip"
    assert target.read_bytes() == DATAPACK
    assert added["installed"]["kind"] == "datapack" and added["checked_by"] is None
    assert added["safety_backup"] and added["undo"]
    undo(client, headers)
    assert not target.exists()


def test_a_dropped_plugin_nobody_lists_is_marked_and_left_out_of_update_all(paper):
    client, headers, folder = paper
    first = upload(client, headers, "Trees-1.0.jar", plugin_jar("Trees")).json()
    assert first["undo"] is None, "nothing was in the folder to put back"
    added = upload(client, headers, "Rocks-1.0.jar", plugin_jar("Rocks")).json()
    assert added["source"] == "file" and added["checked_by"] is None and added["undo"]
    row = plugin_names(client, headers)["Trees"]
    assert row["source"] == "file" and row["checked_by"] is None
    plan = client.get("/api/mods/update-all", headers=headers).json()
    assert plan["updates"] == []
    assert [u["reason"] for u in plan["unchecked"]] == [
        "Added from a file, so there's no list to check it against for updates."
    ] * 2
    undo(client, headers)
    assert not (folder / "Rocks-1.0.jar").exists() and (folder / "Trees-1.0.jar").is_file()


def test_a_file_no_site_lists_asks_first_and_writes_nothing(paper):
    client, headers, folder = paper
    asked = upload(client, headers, "Trees-1.0.jar", plugin_jar("Trees"), confirm=False)
    assert asked.status_code == 400
    problems = asked.json()["problems"]
    assert "can't tell what it is" in problems["confirm"] and len(problems["sha512"]) == 128
    assert "scanned" not in problems["confirm"] and "safe" not in problems["confirm"]
    assert not (folder / "Trees-1.0.jar").exists()
    assert not list(folder.glob(".*.part"))
    added = upload(client, headers, "Trees-1.0.jar", plugin_jar("Trees"))
    assert added.status_code == 200 and (folder / "Trees-1.0.jar").is_file()


def test_a_dropped_file_a_site_lists_is_treated_as_that_sites(paper, sites):
    client, headers, folder = paper
    via = sites.jar(300)
    assert upload(client, headers, "via.jar", via).json()["source"] == "hangar"
    essentials = plugin_jar("essentials", "2.20")
    client.app.state.core.mods.modrinth = PluginModrinth({hashlib.sha512(essentials).hexdigest()})
    assert upload(client, headers, "ess.jar", essentials).json()["source"] == "modrinth"


def test_a_known_bad_file_is_refused(paper, monkeypatch):
    from agent import notes

    client, headers, folder = paper
    data = plugin_jar("Evil")
    monkeypatch.setattr(
        notes, "BAD_FILES", {hashlib.sha512(data).hexdigest(): {"why": "steals logins"}}
    )
    refused = upload(client, headers, "evil.jar", data)
    assert refused.status_code == 400 and "known bad" in refused.json()["detail"]
    assert not list(folder.glob("*.jar"))


# ---------------------------------------------------------------- add from a link
def test_a_github_release_link_lists_its_files_and_installs_the_one_picked(paper, sites):
    client, headers, folder = paper
    read = client.post(
        "/api/mods/link",
        json={"url": "https://github.com/tree-maker/trees/releases/tag/v1.2"},
        headers=headers,
    ).json()
    assert read["kind"] == "github" and read["tag"] == "v1.2"
    assert [f["filename"] for f in read["files"]] == [
        "TreePlugin-1.2.jar",
        "TreePlugin-1.2-sources.jar",
    ]
    added = client.post(
        "/api/mods/link/add",
        json={"version_id": read["files"][0]["version_id"], "confirm": True},
        headers=headers,
    ).json()
    assert added["source"] == "github" and added["checked_by"] is None
    assert (folder / "TreePlugin-1.2.jar").is_file()
    record = client.app.state.core.mods.db.get_setting("mod_source:TreePlugin")
    assert record["project"] == "tree-maker/trees" and record["version"] == "v1.2"

    # Updates come from the repo's release list, matched by the file's name.
    sites.latest = "v1.3"
    plan = client.get("/api/mods/update-all", headers=headers).json()
    assert [(u["latest_version"], u["source"]) for u in plan["updates"]] == [("v1.3", "github")]
    client.post(
        "/api/mods/update",
        json={"filename": "TreePlugin-1.2.jar", "version_id": plan["updates"][0]["version_id"]},
        headers=headers,
    )
    assert (folder / "TreePlugin-1.3.jar").is_file()


@pytest.mark.parametrize(
    "url",
    [
        "https://www.planetminecraft.com/data-pack/trees/",
        "https://www.spigotmc.org/resources/essentialsx.9089/",
        "https://example.com/my-plugin.jar",
        "http://github.com/tree-maker/trees/releases",
    ],
)
def test_any_other_link_says_download_it_in_the_browser(paper, sites, url):
    client, headers, _ = paper
    read = client.post("/api/mods/link", json={"url": url}, headers=headers).json()
    assert read == {
        "kind": "browser",
        "message": "Download it in your browser and drop the file here.",
        "repo": None,
        "tag": None,
        "name": None,
        "prerelease": None,
        "page": None,
        "files": [],
    }
    assert not sites.asked


def test_github_links_are_read_only_in_their_known_shapes():
    assert github.read_link("https://github.com/a/b") == ("a/b", None)
    assert github.read_link("https://github.com/a/b/releases/latest") == ("a/b", None)
    assert github.read_link("https://github.com/a/b/releases/tag/v1.0%2B1") == ("a/b", "v1.0+1")
    assert github.read_link("https://github.com/a/b/releases/download/v2/x.jar") == ("a/b", "v2")
    for other in (
        "https://github.com/a/b/issues",
        "https://gist.github.com/a/b",
        "https://github.com/a",
    ):
        assert github.read_link(other) is None


def test_the_allow_list_grows_only_by_hangar_and_the_one_release_being_read():
    downloads.check_url("https://hangar.papermc.io/api/v1/projects")
    downloads.check_url("https://hangarcdn.papermc.io/plugins/a/b/versions/1/PAPER/b.jar")
    for refused in (
        "https://hangar.papermc.io/admin",
        "https://hangarcdn.papermc.io/other/file.jar",
        "https://api.github.com/repos/tree-maker/trees/releases/latest",
        "https://github.com/tree-maker/trees/releases/download/v1/x.jar",
    ):
        with pytest.raises(downloads.DownloadError):
            downloads.check_url(refused)
    with downloads.allow_releases("tree-maker/trees"):
        downloads.check_url("https://api.github.com/repos/tree-maker/trees/releases/latest")
        downloads.check_url("https://github.com/tree-maker/trees/releases/download/v1/x.jar")
        with pytest.raises(downloads.DownloadError):
            downloads.check_url(
                "https://github.com/tree-maker/trees-evil/releases/download/v1/x.jar"
            )


async def test_hangar_requests_keep_to_twenty_every_five_seconds(monkeypatch):
    from agent.mods import hangar

    clock = [100.0]
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(hangar.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(hangar.asyncio, "sleep", sleep)
    pace = hangar.Pace()
    for _ in range(22):
        await pace.wait()
    assert slept == [5.0, 5.0]
