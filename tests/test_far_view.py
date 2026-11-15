"""Far view (agent/farview.py), through the agent API like the apps use it.

Modrinth is a fake whose versions say which loaders and Minecraft version
they are for and what they need, and whose downloads are real jars, so the
install runs end to end through the real ModManager and the player-only
folder.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent import farview
from agent.mods import sides
from agent.mods.modrinth import ModrinthNotFound

from .test_api import auth, token_for
from .test_curseforge import MC, fabric_jar
from .test_hangar_and_links import app_for, plugin_jar

FABRIC, PLUGIN = ("fabric",), ("paper", "spigot", "bukkit")
# slug: id, title, and its versions: (loaders, needs [(project, pinned version)], number)
PROJECTS = {
    "distanthorizons": ("uCdwusMi", "Distant Horizons", [(FABRIC, [], "3.3.4")]),
    "distant-horizons-support": ("IjY7seTG", "Distant Horizons Support", [(PLUGIN, [], "0.14")]),
    "voxy": ("fxxUqruK", "Voxy", [(FABRIC, [("P7dR8mSH", None), ("AANobbMI", "sodium-7")], "0.2")]),
    "voxy-server-side": (
        "84zcagOb",
        "Voxy Server Side",
        [(FABRIC, [], "0.21"), (PLUGIN, [], "0.21")],
    ),
    "fabric-api": ("P7dR8mSH", "Fabric API", [(FABRIC, [], "0.110")]),
    "sodium": ("AANobbMI", "Sodium", [(FABRIC, [], "7"), (FABRIC, [], "8")]),
}


class FarModrinth:
    name, label = "modrinth", "Modrinth"

    def __init__(self):
        self.by_id = {p[0]: slug for slug, p in PROJECTS.items()}
        self.downloads: list[str] = []

    def _all(self, slug):
        project_id, _, versions = PROJECTS[slug]
        for loaders, needs, number in versions:
            yield {
                "version_id": f"{slug}-{number}",
                "project_id": project_id,
                "version_number": number,
                "release_type": "release",
                "game_versions": [MC],
                "loaders": list(loaders),
                "dependencies": [
                    {"project_id": p, "version_id": v, "type": "required"} for p, v in needs
                ],
                "file": {
                    "filename": f"{slug}-{number}{'-paper' if loaders == PLUGIN else ''}.jar",
                    "url": f"https://cdn.modrinth.com/{slug}.jar",
                    "sha512": "x",
                },
            }

    async def latest_for(self, project, minecraft_version, loaders=None, like=None):
        slug = self.by_id.get(project, project)
        return next((v for v in self._all(slug) if set(v["loaders"]) & set(loaders)), None)

    async def version(self, version_id):
        for slug in PROJECTS:
            for v in self._all(slug):
                if v["version_id"] == version_id:
                    return v
        raise ModrinthNotFound("Modrinth doesn't have that.")

    async def projects(self, ids):
        return {i: {"title": PROJECTS[self.by_id[i]][1], "slug": self.by_id[i]} for i in ids}

    async def download(self, version):
        slug = self.by_id[version["project_id"]]
        self.downloads.append(slug)
        if version["loaders"] == list(PLUGIN):
            data = plugin_jar(PROJECTS[slug][1].replace(" ", ""), version["version_number"])
        else:
            data = fabric_jar(slug.replace("-", "_"), version["version_number"])
        return data, version["file"]["filename"], hashlib.sha256(data).hexdigest()

    async def by_hash(self, sha512):
        return "listed"  # every file here came from Modrinth

    async def close(self):
        pass


def started(tmp_path, monkeypatch, kind):
    client = app_for(tmp_path, monkeypatch, kind)
    client.__enter__()
    core = client.app.state.core
    core.mods.server.mc_version = MC
    core.mods.server.loader_version = "0.16.5"
    (core.mods.config.server_dir / "libraries").mkdir(exist_ok=True)
    core.mods.modrinth = FarModrinth()
    return client, auth(token_for(client))


@pytest.fixture
def fabric(tmp_path, monkeypatch):
    client, headers = started(tmp_path, monkeypatch, "fabric")
    yield client, headers
    client.__exit__(None, None, None)


@pytest.fixture
def paper(tmp_path, monkeypatch):
    client, headers = started(tmp_path, monkeypatch, "paper")
    yield client, headers
    client.__exit__(None, None, None)


@pytest.fixture
def neoforge(tmp_path, monkeypatch):
    client, headers = started(tmp_path, monkeypatch, "neoforge")
    yield client, headers
    client.__exit__(None, None, None)


def base(client) -> Path:
    return client.app.state.core.mods.config.server_dir


def card(client, headers) -> dict:
    got = client.get("/api/servers/test/far-view", headers=headers)
    assert got.status_code == 200, got.text
    return got.json()


def choice(found, name) -> dict:
    return next(c for c in found["choices"] if c["id"] == name)


def choose(client, headers, name):
    return client.post("/api/servers/test/far-view", json={"choice": name}, headers=headers)


def files(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.glob("*.jar")) if folder.is_dir() else []


# ---------------------------------------------------------------- what each choice adds
def test_each_choice_lists_what_it_adds_from_the_mods_own_needs(fabric):
    client, headers = fabric
    (base(client) / "mods" / "fabric-api.jar").write_bytes(fabric_jar("fabric-api"))
    found = card(client, headers)
    assert found["supported"] and found["choice"] == "off" and found["unvisited"] is False
    dh = choice(found, "distanthorizons")
    assert dh["available"] and dh["server"] == ["Distant Horizons"]
    assert dh["players"] == [], "the server's own jar goes in the modpack (Both)"
    voxy = choice(found, "voxy")
    # Fabric API is in already; Sodium is the version Voxy names, not the newest.
    assert voxy["server"] == ["Voxy Server Side"]
    assert voxy["players"] == ["Sodium", "Voxy"]


def test_a_choice_with_nothing_for_this_loader_says_so(neoforge):
    client, headers = neoforge
    voxy = choice(card(client, headers), "voxy")
    assert voxy["available"] is False
    assert voxy["why"] == f"Voxy has nothing for NeoForge on Minecraft {MC} yet."
    refused = choose(client, headers, "voxy")
    assert refused.status_code == 400 and "nothing for NeoForge" in refused.text
    assert files(base(client) / "mods") == []


def test_voxy_on_fabric_goes_on_the_server_and_in_players_only_then_off_takes_only_its_own(
    fabric,
):
    client, headers = fabric
    server, players = base(client) / "mods", base(client) / sides.FOLDER
    (server / "fabric-api.jar").write_bytes(fabric_jar("fabric-api"))
    done = choose(client, headers, "voxy")
    assert done.status_code == 200, done.text
    assert done.json()["added"] == ["voxy-server-side-0.21.jar", "sodium-7.jar", "voxy-0.2.jar"]
    assert files(server) == ["fabric-api.jar", "voxy-server-side-0.21.jar"]
    assert files(players) == ["sodium-7.jar", "voxy-0.2.jar"]
    marks = json.loads((base(client) / sides.MARKS_FILE).read_text("utf-8"))
    assert "both" in marks.values(), "players need the server side too"
    assert card(client, headers)["choice"] == "voxy"

    off = choose(client, headers, "off").json()
    assert sorted(off["removed"]) == ["sodium-7.jar", "voxy-0.2.jar", "voxy-server-side-0.21.jar"]
    assert files(server) == ["fabric-api.jar"], "what was here before stays"
    assert files(players) == []
    assert card(client, headers)["choice"] == "off"


def test_distant_horizons_on_paper_is_the_plugin_and_dh_for_fabric_players(paper):
    client, headers = paper
    dh = choice(card(client, headers), "distanthorizons")
    assert dh["server"] == ["Distant Horizons Support"] and dh["players"] == ["Distant Horizons"]
    assert choose(client, headers, "distanthorizons").status_code == 200
    assert files(base(client) / "plugins") == ["distant-horizons-support-0.14-paper.jar"]
    assert files(base(client) / sides.FOLDER) == ["distanthorizons-3.3.4.jar"]


def test_switching_takes_the_old_choice_away_first(fabric):
    client, headers = fabric
    assert choose(client, headers, "distanthorizons").status_code == 200
    assert choose(client, headers, "voxy").status_code == 200
    assert "distanthorizons-3.3.4.jar" not in files(base(client) / "mods")


# ---------------------------------------------------------------- unvisited land
DH_TOML = """_version = 3
[common.worldGenerator]
\tgeneratorPlan = "SURFACE_THEN_CHUNKS"
\tchunkGeneratorMode = "FEATURES"
[common.lodBuilding]
\tchunkGeneratorMode = "leave me"
"""
DHS_YML = "database_path: x\n#generate_new_chunks: false\nrender_distance: 64\n"


def test_unvisited_land_is_off_by_default_and_written_in_dhs_own_config(fabric):
    client, headers = fabric
    toml = base(client) / farview.DH_CONFIG
    toml.parent.mkdir(exist_ok=True)
    toml.write_text(DH_TOML, "utf-8")
    done = choose(client, headers, "distanthorizons")
    assert done.status_code == 200, done.text
    text = toml.read_text("utf-8")
    assert '\tchunkGeneratorMode = "PRE_EXISTING_ONLY"' in text
    assert 'chunkGeneratorMode = "leave me"' in text and "_version = 3" in text
    body = {"unvisited": True, "load_ahead": False, "radius": 2000, "world": ""}
    got = client.post("/api/servers/test/far-view/options", json=body, headers=headers)
    assert got.status_code == 200 and got.json()["unvisited"] is True
    assert '\tchunkGeneratorMode = "FEATURES"' in toml.read_text("utf-8")


def test_the_dh_plugin_gets_it_too_once_it_made_its_file(paper):
    client, headers = paper
    yml = base(client) / farview.DHS_CONFIG
    choose(client, headers, "distanthorizons")
    assert not yml.exists(), "a file DH hasn't made yet is never made by the app"
    yml.parent.mkdir(parents=True)
    yml.write_text(DHS_YML, "utf-8")
    farview.before_start(client.app.state.core.servers["test"])
    assert (
        yml.read_text("utf-8")
        == "database_path: x\ngenerate_new_chunks: false\nrender_distance: 64\n"
    )


def test_options_refuse_odd_values(fabric):
    client, headers = fabric
    url = "/api/servers/test/far-view/options"
    for body in ({"radius": 50}, {"radius": 40000}, {"world": "../x"}):
        assert client.post(url, json=body, headers=headers).status_code == 422


# ---------------------------------------------------------------- data: backups and rebuild
def test_backups_leave_far_view_data_out(fabric):
    for kept in ("world/region/r.0.0.mca", "world/data/raids.dat", "plugins/x/data.sqlite"):
        assert not farview.is_far_view_data(Path(kept))
    for left in (
        "world/data/DistantHorizons.sqlite",
        "world/DIM-1/data/DistantHorizons.sqlite-wal",
        "world/vss-lod/store.db",
        "plugins/DHSupport/data.sqlite",
    ):
        assert farview.is_far_view_data(Path(left))

    client, headers = fabric
    world = base(client) / "world"
    (world / "data").mkdir(parents=True)
    (world / "data" / "DistantHorizons.sqlite").write_bytes(b"lod")
    (world / "level.dat").write_bytes(b"level")
    made = client.post("/api/backups", json={}, headers=headers)
    assert made.status_code == 200, made.text
    import zipfile

    row = client.app.state.core.servers["test"].backups.get(made.json()["id"])
    with zipfile.ZipFile(row["path"]) as zf:
        names = zf.namelist()
    assert "world/level.dat" in names
    assert not [n for n in names if "DistantHorizons" in n]


def test_rebuild_deletes_only_far_view_data(fabric):
    client, headers = fabric
    world = base(client) / "world"
    for name in ("data/DistantHorizons.sqlite", "vss-lod/a/b.db", "region/r.0.0.mca"):
        (world / name).parent.mkdir(parents=True, exist_ok=True)
        (world / name).write_bytes(b"1234")
    got = client.post("/api/servers/test/far-view/rebuild", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["files"] == 2 and got.json()["size_bytes"] == 8
    assert (world / "region/r.0.0.mca").exists()
    assert not (world / "vss-lod").exists() and not (world / "data/DistantHorizons.sqlite").exists()


# ---------------------------------------------------------------- load the world ahead
def test_the_night_load_runs_only_at_night_with_nobody_on(fabric, monkeypatch):
    client, headers = fabric
    ctx = client.app.state.core.servers["test"]
    (base(client) / "mods" / "chunky.jar").write_bytes(fabric_jar("chunky"))
    body = {"unvisited": False, "load_ahead": True, "radius": 3000, "world": ""}
    client.post("/api/servers/test/far-view/options", json=body, headers=headers)
    sent: list[str] = []
    people = [0]

    async def send(command, internal=False):
        sent.append(command)
        return True

    class Online:
        value = "ONLINE"

    monkeypatch.setattr(ctx.server, "send_command", send)
    monkeypatch.setattr(type(ctx.server), "running", property(lambda self: True))
    monkeypatch.setattr(ctx.server, "state", Online())
    monkeypatch.setattr(ctx.players, "online_count", lambda: people[0])
    run = ctx.night_load.check
    import asyncio

    assert asyncio.run(run(hour=12)) == "paused" and sent == []
    assert asyncio.run(run(hour=2)) == "loading"
    assert sent == ["chunky start world square 0 0 3000"]
    people[0] = 1
    assert asyncio.run(run(hour=2)) == "paused" and sent[-1] == "chunky pause"
    people[0] = 0
    assert asyncio.run(run(hour=3)) == "loading" and sent[-1] == "chunky continue"
    assert asyncio.run(run(hour=6)) == "paused" and sent[-1] == "chunky pause"
    people[0] = None  # nobody established the count: never a guess
    assert asyncio.run(run(hour=2)) == "paused"
