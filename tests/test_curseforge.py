"""CurseForge through our relay, driven through the agent API like the apps
do. The relay, Firebase's sign-in and CurseForge's file servers are one fake
behind the safe downloader's swappable transport; Modrinth is the fake from
test_dependency_resolution. Nothing leaves the machine, and no CurseForge
key exists anywhere in here."""

from __future__ import annotations

import hashlib
import json
import struct
from urllib.parse import parse_qs

import httpx
import pytest

from agent import downloads, modpack
from agent.mods import curseforge
from agent.mods.curseforge import fingerprint, murmur2

from .test_api import auth, token_for
from .test_dependency_resolution import FakeModrinth
from .test_hangar_and_links import app_for, undo, upload, zip_of

MC = "1.21.4"
RELAY = "europe-west1-command-block-d4526.cloudfunctions.net"


def fabric_jar(mod_id: str, version: str = "1.0", depends: dict | None = None) -> bytes:
    meta = {"schemaVersion": 1, "id": mod_id, "version": version, "name": mod_id.title()}
    meta["depends"] = depends or {}
    return zip_of({"fabric.mod.json": json.dumps(meta), f"{mod_id}/Main.class": b"\xca\xfe"})


class FakeCurseForge:
    """Our relay, Firebase's sign-in and CurseForge's file servers.
    ``mods``: CurseForge mod id -> the mod as CurseForge describes it."""

    def __init__(self) -> None:
        self.state = "up"  # down, limited (429) or killed (the function deleted)
        self.asked: list[str] = []
        self.sign_ups = self.refreshes = 0
        self.tokens: set[str] = set()
        self.mods: dict[int, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self.lithium = self.add(394468, "Lithium", downloads=900)
        self.file(394468, 5001, "lithium", "0.11.0", deps=[306612], date="2026-09-01")
        self.add(306612, "Fabric API", downloads=5000)
        self.file(306612, 6001, "fabric-api", "0.110")
        self.add(111, "Lithium Ores", downloads=99_000)
        self.file(111, 1101, "lithiumores", "2.0")
        self.add(222, "Lithium Forge", downloads=50_000)
        self.file(222, 2201, "lithiumforge", "1.0", tags=[MC, "Forge"])
        self.add(333, "Secret Mod", allow=False)
        self.file(333, 3301, "secret", "1.0", url=False)
        self.add(444, "Pulled")
        self.file(444, 4401, "pulled", "1.0", status=6)
        self.add(555, "Shady")
        self.file(555, 5501, "shady", "1.0")
        self.blobs["/files/5501/shady-1.0.jar"] = fabric_jar("shady", "6.6")  # not its sha1

    def add(self, mod_id: int, name: str, downloads: int = 100, allow: bool = True) -> dict:
        self.mods[mod_id] = {
            "id": mod_id,
            "name": name,
            "summary": f"{name} for Fabric",
            "downloadCount": downloads,
            "allowModDistribution": allow,
            "authors": [{"name": "someone"}],
            "links": {"websiteUrl": f"https://www.curseforge.com/minecraft/mc-mods/m{mod_id}"},
            "latestFilesIndexes": [],
            "files": [],
        }
        return self.mods[mod_id]

    def file(
        self,
        mod_id,
        file_id,
        jar_id,
        version,
        tags=(MC, "Fabric"),
        deps=(),
        status=4,
        url=True,
        release=1,
        date="2026-10-01",
        data=None,
    ) -> dict:
        data = data or fabric_jar(jar_id, version)
        name = f"{jar_id}-{version}.jar"
        path = f"/files/{file_id}/{name}"
        self.blobs[path] = data
        entry = {
            "id": file_id,
            "modId": mod_id,
            "displayName": f"{jar_id} {version}",
            "fileName": name,
            "releaseType": release,
            "fileStatus": status,
            "isAvailable": True,
            "hashes": [
                {"value": hashlib.md5(data).hexdigest(), "algo": 2},
                {"value": hashlib.sha1(data).hexdigest(), "algo": 1},
            ],
            "fileDate": f"{date}T00:00:00Z",
            "fileLength": len(data),
            "downloadUrl": f"https://edge.forgecdn.net{path}" if url else None,
            "gameVersions": list(tags),
            "dependencies": [{"modId": d, "relationType": 3} for d in deps],
            "fileFingerprint": fingerprint(data),
        }
        mod = self.mods[mod_id]
        mod["files"].append(entry)
        loader = {"Fabric": 4, "Forge": 1}.get(next((t for t in tags if not t[0].isdigit()), ""))
        mod["latestFilesIndexes"].append(
            {"gameVersion": tags[0], "fileId": file_id, "modLoader": loader}
        )
        return entry

    def jar(self, file_id: int) -> bytes:
        for mod in self.mods.values():
            for f in mod["files"]:
                if f["id"] == file_id:
                    return self.blobs[f"/files/{file_id}/{f['fileName']}"]
        raise KeyError(file_id)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.asked.append(f"{request.method} {url}")
        assert url.host != "api.curseforge.com", "never CurseForge's API itself"
        assert "x-api-key" not in request.headers, "the app has no CurseForge key"
        if url.host == "identitytoolkit.googleapis.com":
            assert url.params["key"].startswith("AIza") and request.method == "POST"
            self.sign_ups += 1
            return httpx.Response(200, json=self.token("idToken", "refreshToken", "expiresIn"))
        if url.host == "securetoken.googleapis.com":
            form = parse_qs(request.content.decode())
            if form["refresh_token"] != ["refresh-1"]:
                return httpx.Response(400, json={"error": {"message": "INVALID_REFRESH_TOKEN"}})
            self.refreshes += 1
            return httpx.Response(200, json=self.token("id_token", "refresh_token", "expires_in"))
        if url.host == "edge.forgecdn.net":  # CurseForge sends files on to its other host
            return httpx.Response(
                302, headers={"location": f"https://mediafilez.forgecdn.net{url.path}"}
            )
        if url.host == "mediafilez.forgecdn.net":
            assert "authorization" not in request.headers, "the sign-in never leaves for the CDN"
            return httpx.Response(200, content=self.blobs[url.path])
        assert url.host == RELAY, f"unexpected request to {url}"
        if self.state == "down":
            raise httpx.ConnectError("down", request=request)
        if self.state == "limited":
            return httpx.Response(429, json={"error": "too many calls, try again in a minute"})
        if self.state == "killed":
            return httpx.Response(404, text="<html>Page not found</html>")
        if request.headers.get("authorization", "").removeprefix("Bearer ") not in self.tokens:
            return httpx.Response(401, json={"error": "sign in first"})
        return httpx.Response(200, json=self.relay(request))

    def token(self, *keys: str) -> dict:
        token = f"token-{len(self.tokens) + 1}"
        self.tokens.add(token)
        return dict(zip(keys, (token, "refresh-1", "3600"), strict=True))

    def relay(self, request: httpx.Request) -> dict:
        path = request.url.path.removeprefix("/curseforge")
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        parts = path.strip("/").split("/")
        if path == "/search":
            assert query["sortField"] == "2" and int(query["pageSize"]) <= 50
            found = [
                self.public(m)
                for m in self.mods.values()
                if query["searchFilter"].lower() in m["name"].lower()
                and str(m.get("classId", 6)) == query["classId"]
            ]
            found.sort(key=lambda m: -m["downloadCount"])  # popularity
            return {"data": found, "pagination": {"totalCount": len(found)}}
        if parts[0] == "mods" and len(parts) == 2:
            return {"data": self.public(self.mods[int(parts[1])])}
        if parts[0] == "mods" and parts[2] == "files":
            files = self.mods[int(parts[1])]["files"]
            if "gameVersion" in query:
                files = [f for f in files if query["gameVersion"] in f["gameVersions"]]
            start = int(query.get("index", 0))
            return {"data": files[start : start + int(query.get("pageSize", 50))]}
        if path == "/fingerprints":
            wanted = set(json.loads(request.content)["fingerprints"])
            matches = [
                {"id": f["modId"], "file": f}
                for m in self.mods.values()
                for f in m["files"]
                if f["fileFingerprint"] in wanted
            ]
            return {"data": {"exactMatches": matches}}
        raise AssertionError(f"not one of the relay's calls: {path}")

    @staticmethod
    def public(mod: dict) -> dict:
        return {k: v for k, v in mod.items() if k != "files"}


@pytest.fixture
def relay(monkeypatch):
    fake = FakeCurseForge()
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def fabric(tmp_path, monkeypatch, relay):
    """A stopped Fabric server on MC that has started once, its token and its mods folder."""
    with app_for(tmp_path, monkeypatch, "fabric") as client:
        core = client.app.state.core
        core.mods.server.mc_version = MC
        core.mods.server.loader_version = "0.16.5"
        (core.mods.config.server_dir / "libraries").mkdir()
        core.mods.modrinth = FakeModrinth({}, mc=MC)
        yield client, auth(token_for(client)), core.mods.mods_dir


def installed(client, headers) -> dict:
    return {m["mod_id"]: m for m in client.get("/api/mods", headers=headers).json()["mods"]}


# ---------------------------------------------------------------- the fingerprint
def test_the_fingerprint_is_murmurhash2_without_whitespace():
    """MurmurHash2 itself, checked with SMHasher's own test: the hashes of
    the keys 0, 0 1, 0 1 2 ... with seeds 256 down to 1, hashed again,
    give 0x27864C1E (SMHasher's published value for MurmurHash2). On top,
    CurseForge takes out tab, newline, carriage return and space first and
    uses seed 1 (as Prism Launcher's CurseForge code does)."""
    key, hashes = bytes(range(256)), b""
    for i in range(256):
        hashes += struct.pack("<I", murmur2(key[:i], 256 - i))
    assert murmur2(hashes, 0) == 0x27864C1E
    assert fingerprint(b"a b\tc\r\nd") == murmur2(b"abcd", 1) == fingerprint(b"abcd")


# ---------------------------------------------------------------- the relay
def test_sign_in_is_anonymous_kept_and_refreshed(fabric, relay, monkeypatch):
    client, headers, _ = fabric
    assert client.get("/api/mods/search?q=lithium", headers=headers).status_code == 200
    assert relay.sign_ups == 1
    assert curseforge.os.environ[curseforge.ENV_KEY] == "refresh-1"  # with the secrets
    # Still good: no new sign-in.
    client.get("/api/mods/search?q=lithium", headers=headers)
    assert relay.sign_ups == 1 and relay.refreshes == 0
    # Near its end, the token is refreshed, not made anew.
    curseforge.SIGN_IN.expires = 0
    client.get("/api/mods/search?q=lithium", headers=headers)
    assert relay.sign_ups == 1 and relay.refreshes == 1
    # The relay turned a token down: signed in again once, then answered.
    relay.tokens.clear()
    found = client.get("/api/mods/search?q=lithium", headers=headers).json()
    assert found["problems"] == [] and relay.refreshes == 2
    assert not any("api.curseforge.com" in a for a in relay.asked)


def test_the_relay_is_asked_at_its_pace():
    assert curseforge.PACE.booked.maxlen == 60 and curseforge.PACE.seconds == 60
    assert curseforge.PAGE_SIZE <= 50


@pytest.mark.parametrize("state", ["down", "limited", "killed"])
def test_curseforge_down_never_blocks_modrinth(fabric, relay, state):
    client, headers, _ = fabric
    client.app.state.core.mods.modrinth = FakeModrinth(
        {"lithium": {"project_id": "gvQqBUqZ", "title": "Lithium", "versions": [("0.11", [])]}},
        mc=MC,
    )
    relay.state = state
    found = client.get("/api/mods/search?q=lithium", headers=headers).json()
    assert [h["source_name"] for h in found["hits"]] == ["Modrinth"]
    assert found["problems"] == ["CurseForge isn't available right now."]


# ---------------------------------------------------------------- search and install
def test_search_names_first_then_popular_only_what_fits(fabric, relay):
    client, headers, _ = fabric
    hits = client.get("/api/mods/search?q=lithium", headers=headers).json()["hits"]
    # Lithium Ores is more popular, but the name typed comes first; the
    # Forge-only one has no file for this server, so it isn't offered.
    assert [h["title"] for h in hits] == ["Lithium", "Lithium Ores"]
    assert hits[0]["project_id"] == "curseforge:394468" and hits[0]["source_name"] == "CurseForge"
    asked = next(a for a in relay.asked if "/curseforge/search" in a)
    assert "sortField=2" in asked and f"gameVersion={MC}" in asked and "modLoaderType=4" in asked


def test_install_checks_sha1_records_only_ids_and_adds_needed_mods(fabric, relay):
    client, headers, folder = fabric
    project = client.get("/api/mods/project/curseforge:394468", headers=headers).json()
    assert project["versions"][0]["version_id"] == "curseforge:394468-5001"
    done = client.post(
        "/api/mods/install",
        json={"project": "curseforge:394468", "install_dependencies": True},
        headers=headers,
    ).json()
    assert done["checked_by"] == "CurseForge" and done["checked_with"] == "sha1"
    assert [d["title"] for d in done["dependencies_installed"]] == ["Fabric-Api"]
    assert (folder / "lithium-0.11.0.jar").read_bytes() == relay.jar(5001)
    assert (folder / "fabric-api-0.110.jar").is_file()
    rows = installed(client, headers)
    assert rows["lithium"]["source_name"] == "CurseForge"
    assert rows["lithium"]["checked_with"] == "sha1"
    record = client.app.state.core.mods.db.get_setting("mod_source:lithium")
    # Only where it came from: no copy of CurseForge's answer.
    assert record == {
        "source": "curseforge",
        "project": "curseforge:394468",
        "version_id": "curseforge:394468-5001",
        "version": "lithium 0.11.0",
    }


def test_a_pulled_file_is_never_offered_and_a_bad_sha1_is_thrown_away(fabric, relay):
    client, headers, folder = fabric
    pulled = client.post("/api/mods/install", json={"project": "curseforge:444"}, headers=headers)
    assert pulled.status_code == 400 and "no Fabric version" in pulled.json()["detail"]
    shady = client.post("/api/mods/install", json={"project": "curseforge:555"}, headers=headers)
    assert shady.status_code == 400 and "checksum" in shady.json()["detail"]
    assert not any(folder.iterdir())


def test_a_known_bad_file_from_curseforge_is_refused(fabric, relay, monkeypatch):
    from agent import notes

    client, headers, folder = fabric
    bad = hashlib.sha512(relay.jar(6001)).hexdigest()
    monkeypatch.setattr(notes, "BAD_FILES", {bad: {"why": "steals logins"}})
    refused = client.post(
        "/api/mods/install", json={"project": "curseforge:306612"}, headers=headers
    )
    assert refused.status_code == 400 and "steals logins" in refused.json()["detail"]
    assert not any(folder.iterdir())


# ---------------------------------------------------------------- Needs you
def test_a_blocked_mod_waits_under_needs_you_and_a_dropped_jar_is_matched(fabric, relay):
    client, headers, folder = fabric
    relay.file(394468, 5002, "lithium", "0.11.1", deps=[306612, 333], date="2026-10-20")
    done = client.post(
        "/api/mods/install",
        json={"project": "curseforge:394468", "install_dependencies": True},
        headers=headers,
    ).json()
    # The rest went ahead.
    assert (folder / "lithium-0.11.1.jar").is_file() and (folder / "fabric-api-0.110.jar").is_file()
    need = {
        "project": "curseforge:333",
        "version_id": "curseforge:333-3301",
        "name": "Secret Mod",
        "filename": "secret-1.0.jar",
        "page": "https://www.curseforge.com/minecraft/mc-mods/m333",
    }
    assert done["needs_you"] == [need]
    assert client.get("/api/mods", headers=headers).json()["needs_you"] == [need]
    assert not any("forgecdn" in a and "secret" in a for a in relay.asked), "no made-up link"

    # The owner downloads it and drops it in under another name: matched by
    # its fingerprint, never by its name.
    added = upload(client, headers, "downloaded (1).jar", relay.jar(3301)).json()
    assert added["source"] == "curseforge" and added["checked_with"] == "sha1"
    assert client.get("/api/mods", headers=headers).json()["needs_you"] == []
    assert installed(client, headers)["secret"]["source"] == "curseforge"


def test_a_blocked_mod_on_its_own_says_why_and_can_be_dismissed(fabric, relay):
    client, headers, _ = fabric
    hits = client.get("/api/mods/search?q=secret", headers=headers).json()["hits"]
    assert hits[0]["blocked"] is True
    refused = client.post("/api/mods/install", json={"project": "curseforge:333"}, headers=headers)
    assert refused.status_code == 400 and "doesn't let other apps" in refused.json()["detail"]
    listed = client.get("/api/mods", headers=headers).json()["needs_you"]
    assert [n["name"] for n in listed] == ["Secret Mod"]
    gone = client.post(
        "/api/mods/needs-you/dismiss", json={"project": "curseforge:333"}, headers=headers
    )
    assert gone.status_code == 200
    assert client.get("/api/mods", headers=headers).json()["needs_you"] == []


def test_a_jar_curseforge_flags_as_malware_is_refused(fabric, relay):
    client, headers, folder = fabric
    refused = upload(client, headers, "pulled.jar", relay.jar(4401))
    assert refused.status_code == 400 and "malware" in refused.json()["detail"]
    assert not any(folder.iterdir())


# ---------------------------------------------------------------- updates
def test_update_all_across_sites_matched_by_fingerprint(fabric, relay):
    client, headers, folder = fabric
    core = client.app.state.core
    core.mods.modrinth = FakeModrinth(
        {"sodium": {"project_id": "AANobbMI", "title": "Sodium", "versions": [("0.6", [])]}},
        mc=MC,
    )
    (folder / "sodium.jar").write_bytes(fabric_jar("sodium", "0.5"))
    core.mods.db.set_setting(
        "mod_source:sodium", {"source": "modrinth", "modrinth_project": "AANobbMI"}
    )
    (folder / "copied-by-hand.jar").write_bytes(relay.jar(5001))  # lithium 0.11.0, by hand
    relay.file(394468, 5002, "lithium", "0.11.1", date="2026-10-20")
    relay.file(394468, 5003, "lithium", "0.12-beta", date="2026-10-25", release=2)

    plan = client.get("/api/mods/update-all", headers=headers).json()
    rows = {r["mod_id"]: r for r in plan["updates"]}
    assert rows["lithium"]["source"] == "curseforge"
    assert rows["lithium"]["latest_version"] == "lithium 0.11.1", "a release, not the beta"
    assert rows["sodium"]["source"] == "modrinth"
    fingerprints = [a for a in relay.asked if a.endswith("/curseforge/fingerprints")]
    assert len(fingerprints) == 1, "one call for the whole folder"

    done = client.post(
        "/api/mods/update-all",
        json={
            "mods": [
                {"filename": r["filename"], "version_id": r["version_id"]} for r in rows.values()
            ]
        },
        headers=headers,
    ).json()
    assert {u["name"] for u in done["updated"]} == {"Lithium", "Sodium"}
    assert (folder / "lithium-0.11.1.jar").read_bytes() == relay.jar(5002)
    assert done["undo"] and done["safety_backup"]
    undo(client, headers)
    assert (folder / "copied-by-hand.jar").is_file() and not (
        folder / "lithium-0.11.1.jar"
    ).exists()

    # Asked once: the hand-copied jars aren't fingerprinted again.
    client.get("/api/mods/update-all", headers=headers)
    assert len([a for a in relay.asked if a.endswith("/curseforge/fingerprints")]) == 1


def test_relay_down_updates_the_rest_and_says_so(fabric, relay):
    client, headers, folder = fabric
    core = client.app.state.core
    core.mods.modrinth = FakeModrinth(
        {"sodium": {"project_id": "AANobbMI", "title": "Sodium", "versions": [("0.6", [])]}},
        mc=MC,
    )
    (folder / "sodium.jar").write_bytes(fabric_jar("sodium", "0.5"))
    core.mods.db.set_setting(
        "mod_source:sodium", {"source": "modrinth", "modrinth_project": "AANobbMI"}
    )
    (folder / "lithium.jar").write_bytes(relay.jar(5001))
    core.mods.db.set_setting(
        "mod_source:lithium", {"source": "curseforge", "project": "curseforge:394468"}
    )
    relay.state = "down"
    plan = client.get("/api/mods/update-all", headers=headers).json()
    assert [r["mod_id"] for r in plan["updates"]] == ["sodium"]
    assert plan["unchecked"] == [
        {
            "mod_id": "lithium",
            "name": "Lithium",
            "filename": "lithium.jar",
            "reason": "CurseForge isn't available right now.",
        }
    ]


def test_a_blocked_update_is_never_offered_as_one(fabric, relay):
    client, headers, folder = fabric
    (folder / "secret.jar").write_bytes(relay.jar(3301))
    relay.file(333, 3302, "secret", "2.0", url=False, date="2026-10-20")
    plan = client.get("/api/mods/update-all", headers=headers).json()
    assert plan["updates"] == []
    (reason,) = [u["reason"] for u in plan["unchecked"] if u["mod_id"] == "secret"]
    assert "doesn't let other apps" in reason and "mc-mods/m333" in reason


async def test_the_background_check_never_asks_the_relay(fabric, relay):
    client, _, folder = fabric
    (folder / "lithium.jar").write_bytes(relay.jar(5001))
    mods = client.app.state.core.mods
    mods.db.set_setting(
        "mod_source:lithium", {"source": "curseforge", "project": "curseforge:394468"}
    )
    assert await mods.check_updates(background=True) == []
    assert not any(RELAY in a for a in relay.asked)


# ---------------------------------------------------------------- modpacks
def cf_pack(files, loader="fabric-0.16.5", minecraft=MC) -> bytes:
    manifest = {
        "minecraft": {"version": minecraft, "modLoaders": [{"id": loader, "primary": True}]},
        "manifestType": "minecraftModpack",
        "manifestVersion": 1,
        "name": "Cozy Pack",
        "version": "1.4",
        "files": [{"projectID": p, "fileID": f, "required": True} for p, f in files],
        "overrides": "overrides",
    }
    return zip_of(
        {
            "manifest.json": json.dumps(manifest),
            "overrides/config/lithium.properties": "mixin.ai=false\n",
            "overrides/resourcepacks/pretty.zip": "client only",
        }
    )


def test_a_curseforge_pack_with_a_blocked_and_a_client_only_mod(fabric, relay):
    client, headers, folder = fabric
    relay.add(777, "Zoom")
    relay.file(777, 7701, "zoom", "1.0", tags=[MC, "Fabric", "Client"])
    pack = cf_pack([(394468, 5001), (333, 3301), (777, 7701)])
    read = client.post(
        "/api/modpacks/inspect?server_id=test",
        files={"file": ("cozy.zip", pack, "application/zip")},
        headers=headers,
    ).json()
    shown = read["pack"]
    assert shown["can_import"] and shown["site"] == "CurseForge"
    assert shown["type"] == "fabric" and shown["minecraft_version"] == MC
    assert shown["download_count"] == 1 and shown["client_only"] == ["zoom-1.0.jar"]
    assert [n["name"] for n in shown["needs_you"]] == ["Secret Mod"]
    assert read["into"]["version_change"] is False

    done = client.post(
        "/api/servers/test/modpack", json={"token": read["token"], "confirm": True}, headers=headers
    ).json()
    assert (folder / "lithium-0.11.0.jar").read_bytes() == relay.jar(5001)
    assert not (folder / "zoom-1.0.jar").exists()
    server_dir = client.app.state.core.mods.config.server_dir
    assert (server_dir / "config" / "lithium.properties").read_text() == "mixin.ai=false\n"
    assert not (server_dir / "resourcepacks").exists()
    assert done["modpack"]["needs_you"] == ["Secret Mod"] and done["undo"]
    assert "CurseForge's sha1" in done["modpack"]["check"]
    assert [n["name"] for n in client.get("/api/mods", headers=headers).json()["needs_you"]] == [
        "Secret Mod"
    ]
    assert modpack.READ == {}, "CurseForge's answers aren't kept after the import"


def test_a_pack_this_app_cant_set_up_is_refused_before_anything(fabric, relay):
    client, headers, _ = fabric
    pack = cf_pack([(394468, 5001)], loader="liteloader-1.0")
    refused = client.post(
        "/api/modpacks/inspect",
        files={"file": ("old.zip", pack, "application/zip")},
        headers=headers,
    )
    assert refused.status_code == 400 and "liteloader" in refused.json()["detail"]
    assert not any(RELAY in a for a in relay.asked), "refused before CurseForge is asked"


def test_a_curseforge_pack_picked_on_curseforge(fabric, relay):
    client, headers, _ = fabric
    relay.add(900, "Cozy Pack")["classId"] = 4471
    zipped = cf_pack([(394468, 5001)])
    entry = relay.file(900, 9001, "cozy", "1.4", tags=[MC], data=zipped)
    entry["fileName"] = "Cozy Pack-1.4.zip"
    relay.blobs["/files/9001/Cozy Pack-1.4.zip"] = zipped
    entry["downloadUrl"] = "https://edge.forgecdn.net/files/9001/Cozy Pack-1.4.zip"
    found = client.get("/api/modpacks/curseforge/search?q=cozy", headers=headers).json()
    assert [h["project_id"] for h in found["hits"]] == ["curseforge:900"]
    read = client.post(
        "/api/modpacks/curseforge",
        json={"project": "curseforge:900", "server_id": "test"},
        headers=headers,
    ).json()
    assert read["pack"]["name"] == "Cozy Pack" and read["pack"]["download_count"] == 1
    assert client.delete(f"/api/modpacks/{read['token']}", headers=headers).status_code == 200
    assert modpack.READ == {}
