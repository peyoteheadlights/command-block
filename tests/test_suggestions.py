"""Add-on suggestions and copying add-ons to another server, driven through
the agent API like the apps do.

Modrinth is a fake with a build list per Minecraft version and the side
fields the real one has; the suggestion list is a test notes file's
``recommended_addons`` checked by the real loader. Measured lag and memory
are fake metrics readings. Nothing leaves the machine.
"""

from __future__ import annotations

import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from agent import notes, suggestions
from agent.main import create_app
from agent.security import permissions as P
from agent.security.auth import hash_password

from .conftest import PASSWORD, build_multi_config
from .test_dependency_resolution import jar_bytes
from .test_hangar_and_links import plugin_jar
from .test_helpers import add_helper
from .test_keeping_current import notes_body
from .test_mods import make_jar

NEW, OLD = "1.21.4", "1.21.1"
EVERY = ["fabric", "quilt", "forge", "neoforge", "paper", "purpur", "leaf"]


def entry(project, kind="good_to_have", players="optional", **extra) -> dict:
    return {
        "project": project,
        "name": project.title(),
        "types": extra.pop("types", EVERY),
        "kind": kind,
        "players_need": players,
        **extra,
    }


ENTRIES = [
    entry("lithium", "performance"),
    entry("lmd", "performance", changes_gameplay=True),
    entry("c2me", "performance", technical_only=True),
    entry("chunky", played_by=["kids"]),
    entry("voice", players="both", group="voice", played_by=["friends"]),
    entry("essentials", changes_gameplay=True, group="starter", played_by=["friends"]),
    entry("ghost"),  # no build for any version here
    entry("library"),  # a library: only ever added as something another needs
    entry("betamod"),  # only a beta build
    entry("sodium-extra", "often_used_with", **{"with": ["sodium"]}),
    entry("bluemap", group="maps"),
]

# slug -> facts: builds per Minecraft version, loaders, sides, downloads
PROJECTS = {
    "lithium": {"builds": {NEW: "0.15.0", OLD: "0.13.0"}},
    "lmd": {"builds": {NEW: "1.0"}},
    "c2me": {"builds": {NEW: "0.3"}},
    "chunky": {"builds": {NEW: "1.5", OLD: "1.4"}},
    "voice": {"builds": {NEW: "2.5", OLD: "2.4"}},
    "essentials": {"builds": {NEW: "2.21"}, "loaders": ["paper"]},
    "ghost": {"builds": {}},
    "library": {"builds": {NEW: "9"}, "categories": ["library"]},
    "betamod": {"builds": {NEW: "0.1"}, "release": "beta"},
    "sodium-extra": {"builds": {NEW: "0.6"}, "server": "unsupported", "client": "required"},
    "bluemap": {"builds": {NEW: "5.4"}, "mod_id": "bluemap"},
    "krypton": {"builds": {NEW: "0.3"}},
}


class FakeModrinth:
    name, label = "modrinth", "Modrinth"

    def __init__(self, server_type) -> None:
        self.type = server_type
        self.asked: list[str] = []
        self.issued: dict[str, dict] = {}

    def _info(self, slug: str) -> dict:
        p = PROJECTS[slug]
        return {
            "project_id": f"id-{slug}",
            "slug": slug,
            "title": slug.title(),
            "description": f"What {slug} does.",
            "categories": p.get("categories", []),
            "downloads": 1000 + len(slug),
            "client_side": p.get("client", "optional"),
            "server_side": p.get("server", "optional"),
            "page": f"https://modrinth.com/mod/{slug}",
        }

    def _slug(self, project: str) -> str:
        return project.removeprefix("id-")

    async def projects(self, ids):
        self.asked.append("projects")
        found = {}
        for project in ids:
            slug = self._slug(project)
            if slug in PROJECTS:
                found[project] = self._info(slug)
        return found

    async def project(self, project):
        return self._info(self._slug(project))

    async def latest_for(self, project, minecraft, loaders=None, like=None):
        slug = self._slug(project)
        p = PROJECTS.get(slug)
        loaders = loaders or self.type.modrinth_loaders
        number = p and p["builds"].get(minecraft)
        if not number or not set(loaders) & set(p.get("loaders", ["fabric", "quilt"])):
            return None
        loader = next(x for x in loaders if x in p.get("loaders", ["fabric", "quilt"]))
        version = {
            "version_id": f"{slug}-{minecraft}",
            "project_id": f"id-{slug}",
            "version_number": number,
            "release_type": p.get("release", "release"),
            "game_versions": [minecraft],
            "loaders": [loader],
            "dependencies": [],
            "file": {
                "filename": f"{slug}-{number}.jar",
                "url": f"https://cdn.modrinth.com/{slug}.jar",
                "sha512": "x",
            },
        }
        self.issued[version["version_id"]] = version
        return version

    async def version(self, version_id):
        return self.issued[version_id]

    async def download(self, version):
        slug = self._slug(version["project_id"])
        if "paper" in version["loaders"]:
            data = plugin_jar(slug.title(), version["version_number"])
        else:
            data = jar_bytes(PROJECTS[slug].get("mod_id", slug), version["version_number"])
        return data, version["file"]["filename"], hashlib.sha256(data).hexdigest()

    async def versions(self, project, minecraft=None, loaders=None):
        found = await self.latest_for(project, minecraft or NEW, loaders)
        return [found] if found else []

    async def by_hash(self, sha512):
        return None

    async def close(self):
        pass


def make_client(
    tmp_path, monkeypatch, kinds=("fabric", "fabric"), versions=(NEW, OLD), played_by=""
):
    config = build_multi_config(tmp_path)
    for server, kind in zip(config._data["servers"], kinds, strict=True):
        server.update(type=kind, played_by=played_by)
    config._server_cache.clear()
    monkeypatch.setenv("MCSC_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MCSC_ADMIN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))
    monkeypatch.delenv("MCSC_API_TOKEN", raising=False)
    client = TestClient(create_app(config))
    client.__enter__()
    token = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    client.headers["Authorization"] = f"Bearer {token.json()['token']}"
    core = client.app.state.core
    for ctx, version in zip(core.servers.values(), versions, strict=True):
        ctx.server.mc_version = version
        ctx.mods.modrinth = FakeModrinth(ctx.config.server_type)
        ctx.metrics.history = lambda hours=1: []
    return client


@pytest.fixture
def listed(monkeypatch):
    """The test notes file's list, checked by the real loader."""
    found = notes.parse(
        __import__("json").dumps(notes_body(recommended_addons=ENTRIES)).encode(),
        0,
        time.time(),
        "0.9.0",
    )
    monkeypatch.setattr(notes, "RECOMMENDED", found.recommended_addons)
    suggestions._checked.clear()
    return found.recommended_addons


@pytest.fixture
def make(tmp_path, monkeypatch, listed):
    """Agents with two servers, closed after the test."""
    made = []

    def build(**kwargs):
        made.append(make_client(tmp_path / str(len(made)), monkeypatch, **kwargs))
        return made[-1]

    yield build
    for client in made:
        client.__exit__(None, None, None)


@pytest.fixture
def fabric(make):
    return make()


def ctx_of(client, server_id="survival"):
    return client.app.state.core.servers[server_id]


def get(client, path="suggestions", server="survival", **params):
    response = client.get(f"/api/servers/{server}/{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def names(rows) -> list[str]:
    return [r["project"] if "project" in r else r["name"] for r in rows]


def slow(ctx, tps=9.0, n=20):
    rows = [{"ts": time.time() - 60 * i, "tps": tps, "ram_percent": 40.0} for i in range(n)]
    ctx.metrics.history = lambda hours=1: rows


def hand_copy(ctx, mod_id):
    make_jar(ctx.config.mods_dir / f"{mod_id}.jar", mod_id)
    ctx.mods._cache = None


async def install(client, project, server="survival"):
    ctx = ctx_of(client, server)
    await ctx.mods.install_from_site(project, "admin")


# ---------------------------------------------------------------- the notes kind
def test_the_shipped_list_is_used_until_a_signed_one_arrives(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(notes, "SHIPPED", Path(__file__).parent.parent / "data" / "catalog.json")
    notes.load_shipped()
    projects = {e["project"] for e in notes.RECOMMENDED}
    assert {"lithium", "ferrite-core", "krypton", "chunky", "simple-voice-chat"} <= projects
    assert "memory-leak-fix" not in projects
    assert all(e["source"] == "modrinth" for e in notes.RECOMMENDED)


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "best"},
        {"types": ["bedrock"]},
        {"players_need": "maybe"},
        {"changes_gameplay": "no"},
        {"group": "fun"},
        {"kind": "often_used_with"},  # needs a "with"
        {"with": ["sodium"]},  # only for often_used_with
        {"played_by": ["everyone"]},
        {"project": "../../x"},
    ],
)
def test_a_bad_suggestion_refuses_the_whole_file(bad):
    body = notes_body(recommended_addons=[{**entry("chunky"), **bad}])
    with pytest.raises(notes.NotesRefused):
        notes.parse(__import__("json").dumps(body).encode(), 0, time.time(), "0.9.0")


# ---------------------------------------------------------------- Good to have
def test_good_to_have_only_shows_what_fits_this_server(fabric):
    found = get(fabric)
    assert found["problem"] is None and found["minecraft_version"] == NEW
    shown = names(found["good_to_have"])
    # No build, a library and a beta build are left out in Simple mode.
    assert shown == ["chunky", "voice", "bluemap"]
    chunky = found["good_to_have"][0]
    assert chunky["changes_gameplay"] is False and chunky["version"] == "1.5"
    assert chunky["downloads"]["source"] == "Modrinth" and chunky["downloads"]["read_at"]
    assert chunky["downloads"]["count"] == 1006
    # Technical also shows the beta, marked.
    beta = [s for s in get(fabric, technical=True)["good_to_have"] if s["project"] == "betamod"]
    assert beta[0]["technical_only"] and beta[0]["release_type"] == "beta"


async def test_an_add_on_already_here_isnt_suggested(fabric):
    await install(fabric, "chunky")
    assert "chunky" not in names(get(fabric)["good_to_have"])


def test_nothing_is_checked_before_the_version_is_known(fabric):
    ctx_of(fabric).server.mc_version = None
    found = get(fabric)
    assert found["problem"] == suggestions.START_ONCE and found["good_to_have"] == []


def test_plugin_servers_get_groups(make, monkeypatch):
    client = make(kinds=("paper", "paper"))
    monkeypatch.setitem(PROJECTS["chunky"], "loaders", ["fabric", "quilt", "paper"])
    found = get(client)
    groups = {s["project"]: s["group"] for s in found["good_to_have"]}
    assert groups == {"chunky": "", "essentials": "starter"}
    assert found["good_to_have"][1]["changes_gameplay"] is True


# ---------------------------------------------------------------- For your players
async def test_client_only_and_both_sides_go_to_for_your_players(fabric):
    hand_copy(ctx_of(fabric), "sodium")
    found = get(fabric)
    # Client-only: listed for players, never offered for the server.
    assert names(found["for_your_players"]) == ["Sodium-Extra"]
    assert found["for_your_players"][0]["also_on_server"] is False
    assert "sodium-extra" not in names(found["often_used_with"] + found["good_to_have"])
    # Players need it too: offered for the server, and once it's there, listed.
    assert [s["place"] for s in found["good_to_have"] if s["project"] == "voice"] == ["both"]
    await install(fabric, "voice")
    players = get(fabric)["for_your_players"]
    assert {"name": "Voice", "also_on_server": True}.items() <= players[-1].items()


# ---------------------------------------------------------------- speed-ups
def test_no_measured_problem_no_speed_ups(fabric):
    found = get(fabric, technical=True)
    assert found["performance"] == [] and found["performance_reason"] is None


def test_lag_on_fabric_offers_the_safe_speed_ups_first(fabric):
    slow(ctx_of(fabric))
    found = get(fabric)
    assert found["performance_reason"]["why"] == "lag"
    assert "below 18 of 20 in 20 of 20 readings" in found["performance_reason"]["evidence"]
    assert names(found["performance"]) == ["lithium"]
    # Gameplay-changing and alpha ones only in Technical.
    assert names(get(fabric, technical=True)["performance"]) == ["lithium", "lmd", "c2me"]


def test_full_memory_counts_too(fabric):
    rows = [{"ts": time.time(), "tps": None, "ram_percent": 95.0} for _ in range(10)]
    ctx_of(fabric).metrics.history = lambda hours=1: rows
    assert get(fabric)["performance_reason"]["why"] == "memory"


def test_paper_gets_its_own_settings_saved_as_a_change_with_undo(make):
    client = make(kinds=("paper", "paper"))
    slow(ctx_of(client))
    settings = get(client)["performance"][0]
    assert settings["kind"] == "settings"
    assert settings["now"] == {"view-distance": 10, "simulation-distance": 10}
    assert settings["values"] == {"view-distance": "8", "simulation-distance": "6"}
    saved = client.put("/api/servers/survival/game-settings", json={"values": settings["values"]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["undo"]
    assert get(client)["performance"] == []  # nothing left to lower


def test_vanilla_gets_change_to_fabric_with_lithium(make):
    client = make(kinds=("vanilla", "fabric"))
    assert get(client)["performance"] == []
    slow(ctx_of(client))
    assert get(client)["performance"] == [
        {"kind": "change_type", "name": "Change to Fabric with Lithium", "type": "fabric"}
    ]


# ---------------------------------------------------------------- Simple: one at a time
def addon_recs(client) -> list[dict]:
    found = get(client, "recommendations")["recommendations"]
    return [r for r in found if r["id"].startswith("suggest_")]


def test_simple_mode_shows_one_safe_suggestion_at_a_time(fabric):
    get(fabric)  # the check is kept; the Overview reads it without asking anyone
    shown = addon_recs(fabric)
    assert [r["id"] for r in shown] == ["suggest_chunky"]
    assert shown[0]["details"]["fix"] == {"kind": "add_on", "project": "chunky", "name": "Chunky"}
    assert "hand-checked list" in shown[0]["evidence"]
    for verb in ("dismiss", "snooze"):
        current = addon_recs(fabric)[0]["id"]
        moved = fabric.post(
            f"/api/servers/survival/recommendations/{current}", json={"action": verb}
        )
        assert moved.status_code == 200, moved.text
    # Never one that changes gameplay (essentials) or is only for players.
    assert [r["id"] for r in addon_recs(fabric)] == ["suggest_bluemap"]


def test_who_the_server_is_for_picks_the_first_one(make):
    client = make(played_by="friends")
    get(client)
    assert [r["id"] for r in addon_recs(client)] == ["suggest_voice"]


def test_a_measured_slowdown_comes_first(fabric):
    slow(ctx_of(fabric))
    get(fabric)
    (shown,) = addon_recs(fabric)
    assert shown["id"] == "suggest_lithium" and "Game speed was below" in shown["evidence"]


# ---------------------------------------------------------------- the live map
async def test_an_installed_map_gets_a_link_and_its_port_is_counted(fabric, monkeypatch):
    from agent import joininfo

    monkeypatch.setattr(joininfo, "tailscale_address", lambda: {"address": "100.64.1.2"})
    ctx = ctx_of(fabric)
    await install(fabric, "bluemap")
    conf = ctx.config.server_dir / "config" / "bluemap" / "webserver.conf"
    found = get(fabric)["maps"]
    assert found[0]["port"] == 8100 and "default" in found[0]["source"]
    conf.parent.mkdir(parents=True)
    conf.write_text("enabled: true\nport: 8200\n", encoding="utf-8")
    (shown,) = get(fabric)["maps"]
    assert shown["link"] == "http://100.64.1.2:8200/" and shown["port"] == 8200
    assert ctx.core.ports.used()[("tcp", 8200)] == "survival"


async def test_no_tailscale_no_link(fabric, monkeypatch):
    from agent import joininfo

    monkeypatch.setattr(joininfo, "tailscale_address", lambda: {"address": None})
    await install(fabric, "bluemap")
    (shown,) = get(fabric)["maps"]
    assert shown["link"] is None and "Tailscale isn't connected" in shown["problem"]


# ---------------------------------------------------------------- copy add-ons
def check_copy(client, filenames, target="creative", **headers):
    return client.post(
        "/api/servers/survival/mods/copy/check",
        json={"target": target, "filenames": filenames},
        **headers,
    )


async def test_copy_between_fabric_servers_of_different_versions(fabric):
    survival, creative = ctx_of(fabric), ctx_of(fabric, "creative")
    for project in ("lithium", "krypton"):
        await install(fabric, project)
    hand_copy(survival, "handmade")
    (survival.config.server_dir / "config" / "lithium.properties").write_text("a=1\n")
    assert get(fabric, "mods/copy")["targets"] == [
        {"id": "creative", "name": "Creative", "type": "fabric"}
    ]
    plan = check_copy(fabric, ["lithium-0.15.0.jar", "krypton-0.3.jar", "handmade.jar"]).json()
    assert [(c["name"], c["version"]) for c in plan["coming"]] == [("Lithium", "0.13.0")]
    assert plan["coming"][0]["settings"] == ["config/lithium.properties"]
    why = {c["name"]: c["why"] for c in plan["cant_come"]}
    assert "no Fabric version of it for Minecraft 1.21.1" in why["Krypton"]
    assert "added by hand" in why["Handmade"]

    done = fabric.post(
        "/api/servers/survival/mods/copy",
        json={"target": "creative", "filenames": ["lithium-0.15.0.jar", "handmade.jar"]},
    )
    assert done.status_code == 200, done.text
    assert done.json()["safety_backup"] and done.json()["undo"]
    assert (creative.config.mods_dir / "lithium-0.13.0.jar").is_file()
    assert (creative.config.server_dir / "config" / "lithium.properties").read_text() == "a=1\n"
    assert not (creative.config.mods_dir / "handmade.jar").exists()

    undone = fabric.post("/api/servers/creative/changes/undo", json={})
    assert undone.status_code == 200, undone.text
    assert not (creative.config.mods_dir / "lithium-0.13.0.jar").exists()
    assert not (creative.config.server_dir / "config" / "lithium.properties").exists()


@pytest.fixture
def paper(make):
    return make(kinds=("paper", "paper"), versions=(NEW, NEW))


async def test_copy_between_paper_servers_brings_the_plugin_folder(paper, tmp_path):
    survival, creative = ctx_of(paper), ctx_of(paper, "creative")
    await install(paper, "essentials")
    folder = survival.config.mods_dir / "Essentials"
    folder.mkdir()
    (folder / "config.yml").write_text("homes: 3\n")
    (folder / "elsewhere").symlink_to(tmp_path)  # a link is never followed
    done = paper.post(
        "/api/servers/survival/mods/copy",
        json={"target": "creative", "filenames": ["essentials-2.21.jar"]},
    )
    assert done.status_code == 200, done.text
    copied = creative.config.mods_dir / "Essentials"
    assert (copied / "config.yml").read_text() == "homes: 3\n"
    assert not (copied / "elsewhere").exists()


async def test_copy_needs_change_mods_on_both_servers(fabric):
    owner = dict(fabric.headers)
    fabric.headers.pop("Authorization")
    sam = add_helper(fabric, owner)
    grants = {"survival": sorted(P.grant({P.MODS_MANAGE}))}
    response = fabric.put("/api/accounts/sam", json={"grants": grants}, headers=owner)
    assert response.status_code == 200, response.text
    targets = fabric.get("/api/servers/survival/mods/copy", headers=sam)
    assert targets.json()["targets"] == []
    refused = check_copy(fabric, ["x.jar"], headers=sam)
    assert refused.status_code == 403
