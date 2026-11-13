"""The "Still fit?" card (agent/mods/fit.py): after a mod or version change,
which other mods need a different version, which have none, and which
nothing said anything about. Modrinth is a fake behind the client's
transport; CurseForge is the relay fake from test_curseforge."""

from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from agent.events import Event
from agent.mods import fit
from agent.mods.modrinth import sides

from .test_curseforge import fabric, fabric_jar, relay  # noqa: F401  (fixtures)
from .test_mods import make_jar, manager  # noqa: F401  (fixture)


def sha1_of(path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def version(vid, number, sha1="0" * 40, game="1.20.1", deps=()):
    return {
        "id": vid,
        "project_id": f"p-{vid}",
        "version_number": number,
        "version_type": "release",
        "game_versions": [game],
        "loaders": ["fabric"],
        "dependencies": [{"project_id": p, "dependency_type": kind} for p, kind in deps],
        "files": [{"primary": True, "filename": f"{vid}.jar", "hashes": {"sha1": sha1}}],
    }


class FakeModrinth:
    """``now``: sha1 -> what the installed file is; ``newest``: sha1 -> the
    newest version for the asked Minecraft version (left out: none)."""

    def __init__(self, now=None, newest=None, down=False):
        self.now, self.newest, self.down = now or {}, newest or {}, down
        self.asked: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.asked.append((request.url.path, body))
        if self.down:
            return httpx.Response(503)
        if request.url.path.endswith("/version_files/update"):
            assert body["algorithm"] == "sha1" and body["loaders"] == ["fabric"]
            found = self.newest
        elif request.url.path.endswith("/version_files"):
            found = self.now
        else:
            raise AssertionError(f"unexpected {request.url}")
        return httpx.Response(200, json={h: v for h, v in found.items() if h in body["hashes"]})


@pytest.fixture
def ctx(manager):  # noqa: F811
    return SimpleNamespace(mods=manager, db=manager.db, bus=manager.bus, server_id="test")


def use(manager, fake):  # noqa: F811
    manager.modrinth._client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return fake


def folder(manager):  # noqa: F811
    manager.mods_dir.mkdir(parents=True, exist_ok=True)
    return manager.mods_dir


# ---------------------------------------------------------------- after one mod changes
async def test_an_addon_that_needs_the_old_version_gets_a_newer_one(ctx, manager):  # noqa: F811
    mods = folder(manager)
    make_jar(mods / "create.jar", "create", "6.0.0")
    addon = make_jar(mods / "rails.jar", "rails", "1.0.0", depends={"create": "<6"})
    make_jar(mods / "other.jar", "other", "1.0.0")  # nothing to do with Create
    fake = use(
        manager,
        FakeModrinth(newest={sha1_of(addon): version("R2", "2.0.0")}),
    )

    card = await fit.check_fit(ctx, "Create 6.0.0", {"create"})

    assert [(r["name"], r["from"], r["to"], r["version_id"]) for r in card["needs_change"]] == [
        ("Rails", "1.0.0", "2.0.0", "R2")
    ]
    assert "needs Create" in card["needs_change"][0]["why"]
    assert card["no_fit"] == card["cant_tell"] == []
    # One batched call; the mod just changed and the unrelated one aren't asked about.
    assert [path for path, _ in fake.asked] == ["/v2/version_files", "/v2/version_files/update"]
    assert fake.asked[1][1]["hashes"] == [sha1_of(addon)]
    assert fake.asked[1][1]["game_versions"] == ["1.20.1"]


async def test_no_fitting_version_and_a_mod_from_a_file(ctx, manager):  # noqa: F811
    mods = folder(manager)
    make_jar(mods / "create.jar", "create", "6.0.0")
    make_jar(mods / "rails.jar", "rails", "1.0.0", depends={"create": "<6"})
    make_jar(mods / "hand.jar", "hand", "1.0.0", depends={"create": "<6"})
    manager.db.set_setting("mod_source:hand", {"source": "file"})
    use(manager, FakeModrinth())

    card = await fit.check_fit(ctx, "Create 6.0.0", {"create"})

    rows = {r["mod_id"]: r["why"] for r in card["no_fit"]}
    assert "No version for Minecraft 1.20.1 yet" in rows["rails"]
    assert "added from a file" in rows["hand"]
    assert card["needs_change"] == []


async def test_a_newly_added_mod_asks_for_a_newer_library(ctx, manager):  # noqa: F811
    """The one just added on purpose is never the one to change."""
    mods = folder(manager)
    lib = make_jar(mods / "lib.jar", "lib", "1.0.0")
    make_jar(mods / "new.jar", "new", "1.0.0", depends={"lib": ">=2"})
    use(manager, FakeModrinth(newest={sha1_of(lib): version("L2", "2.1.0")}))

    card = await fit.check_fit(ctx, "New 1.0.0", {"new"})

    assert [(r["mod_id"], r["to"]) for r in card["needs_change"]] == [("lib", "2.1.0")]


async def test_a_new_version_that_another_range_shuts_out_is_cant_tell(ctx, manager):  # noqa: F811
    mods = folder(manager)
    lib = make_jar(mods / "lib.jar", "lib", "1.0.0")
    make_jar(mods / "new.jar", "new", "1.0.0", depends={"lib": ">=2"})
    make_jar(mods / "old.jar", "old", "1.0.0", depends={"lib": "<2"})
    use(manager, FakeModrinth(newest={sha1_of(lib): version("L2", "2.1.0")}))

    card = await fit.check_fit(ctx, "New 1.0.0", {"new"})

    assert card["needs_change"] == []
    assert "Can't tell if 2.1.0 fits Old's range" in card["cant_tell"][0]["why"]


async def test_the_sites_incompatible_link_is_never_offered(ctx, manager):  # noqa: F811
    mods = folder(manager)
    make_jar(mods / "create.jar", "create", "6.0.0")
    addon = make_jar(mods / "rails.jar", "rails", "1.0.0", depends={"create": "<6"})
    manager.db.set_setting("mod_source:create", {"source": "modrinth", "modrinth_project": "CR"})
    newer = version("R2", "2.0.0", deps=[("CR", "incompatible")])
    use(manager, FakeModrinth(newest={sha1_of(addon): newer}))

    card = await fit.check_fit(ctx, "Create 6.0.0", {"create"})

    assert card["needs_change"] == []
    assert "doesn't work with Create" in card["cant_tell"][0]["why"]


async def test_modrinth_down_is_cant_tell_never_fits(ctx, manager):  # noqa: F811
    mods = folder(manager)
    make_jar(mods / "create.jar", "create", "6.0.0")
    make_jar(mods / "rails.jar", "rails", "1.0.0", depends={"create": "<6"})
    use(manager, FakeModrinth(down=True))

    card = await fit.check_fit(ctx, "Create 6.0.0", {"create"})

    assert card["needs_change"] == card["no_fit"] == []
    assert "Modrinth" in card["cant_tell"][0]["why"]


# ---------------------------------------------------------------- after a version change
async def test_after_a_minecraft_version_change_every_mod_is_checked(ctx, manager):  # noqa: F811
    mods = folder(manager)
    manager.server.mc_version = None
    manager.server.pending_version = {"minecraft_version": "1.21.4", "loader_version": "0.16.9"}
    old = make_jar(mods / "old.jar", "old", "1.0.0", depends={"minecraft": "1.20.x"})
    listed = make_jar(mods / "listed.jar", "listed", "1.0.0")
    make_jar(mods / "hand.jar", "hand", "1.0.0")
    manager.db.set_setting("mod_source:hand", {"source": "file"})
    gone = make_jar(mods / "gone.jar", "gone", "1.0.0")
    fake = use(
        manager,
        FakeModrinth(
            now={
                sha1_of(listed): version("LI", "1.0.0", sha1_of(listed), game="1.21.4"),
                sha1_of(gone): version("GO", "1.0.0", sha1_of(gone), game="1.20.1"),
            },
            newest={
                sha1_of(old): version("O2", "2.0.0", game="1.21.4"),
                sha1_of(listed): version("LI", "1.0.0", sha1_of(listed), game="1.21.4"),
            },
        ),
    )

    card = await fit.check_fit(ctx, "Minecraft 1.21.4", None)

    assert [(r["mod_id"], r["to"]) for r in card["needs_change"]] == [("old", "2.0.0")]
    assert "Minecraft 1.21.4 isn't in its range" in card["needs_change"][0]["why"]
    assert [r["mod_id"] for r in card["no_fit"]] == ["gone"]
    assert "doesn't list 1.0.0 for Minecraft 1.21.4" in card["no_fit"][0]["why"]
    # Nothing said about the jar from a file: can't tell, never "fits".
    assert [r["mod_id"] for r in card["cant_tell"]] == ["hand"]
    assert len(fake.asked) == 2, "two calls for every Modrinth mod"
    assert fake.asked[1][1]["game_versions"] == ["1.21.4"]


async def test_nothing_to_check_before_the_version_is_known(ctx, manager):  # noqa: F811
    manager.server.mc_version = None
    make_jar(folder(manager) / "a.jar", "a", "1.0.0")
    assert not fit.shown(await fit.check_fit(ctx, "Minecraft ?", None))


def test_curseforge_mods_are_checked_through_the_relay(fabric, relay):  # noqa: F811
    client, headers, mods = fabric
    core = client.app.state.core
    (mods / "lib.jar").write_bytes(fabric_jar("lib", "1.0"))
    (mods / "addon.jar").write_bytes(fabric_jar("addon", "1.0", depends={"lib": "<2"}))
    (mods / "lib.jar").write_bytes(fabric_jar("lib", "2.0"))
    relay.add(77, "Addon")
    relay.file(77, 7001, "addon", "1.1")
    relay.file(77, 7002, "addon", "1.2", tags=("1.20.1", "Fabric"))
    core.mods.db.set_setting(
        "mod_source:addon", {"source": "curseforge", "project": "curseforge:77"}
    )
    ctx = SimpleNamespace(mods=core.mods, db=core.mods.db, bus=core.mods.bus, server_id="x")

    card = asyncio.run(fit.check_fit(ctx, "Lib 2.0", {"lib"}))

    assert [(r["mod_id"], r["to"]) for r in card["needs_change"]] == [("addon", "addon 1.1")]
    assert card["needs_change"][0]["version_id"] == "curseforge:77-7001"


# ---------------------------------------------------------------- when it runs and what the apps get
async def test_several_quick_changes_are_checked_once(ctx, manager, monkeypatch):  # noqa: F811
    asked = []

    async def refresh(_ctx, about, ids):
        asked.append((about, ids))

    monkeypatch.setattr(fit, "WAIT", 0.01)
    monkeypatch.setattr(fit, "refresh", refresh)
    later = fit.Later(ctx)
    for name in ("a", "b"):
        later.heard(
            Event(type="mod_updated", data={"mod": {"mod_id": name, "name": name, "version": "2"}})
        )
    later.heard(Event(type="player_joined"))
    await asyncio.sleep(0.05)
    assert asked == [("these changes", {"a", "b"})]

    later.heard(Event(type="version_changed", data={"minecraft_version": "1.21.4"}))
    later.heard(Event(type="mod_installed", data={"source": "file"}))  # a datapack: no mod
    await asyncio.sleep(0.05)
    assert asked[1] == ("Minecraft 1.21.4", None)


async def test_the_card_is_saved_and_the_apps_are_told(ctx, manager):  # noqa: F811
    mods = folder(manager)
    make_jar(mods / "create.jar", "create", "6.0.0")
    addon = make_jar(mods / "rails.jar", "rails", "1.0.0", depends={"create": "<6"})
    use(manager, FakeModrinth(newest={sha1_of(addon): version("R2", "2.0.0")}))

    await fit.refresh(ctx, "Create 6.0.0", {"create"})

    saved = manager.db.get_setting(fit.SETTING)
    assert saved["about"] == "Create 6.0.0" and saved["needs_change"][0]["to"] == "2.0.0"
    assert saved["checked_at"]

    (mods / "rails.jar").unlink()
    await fit.refresh(ctx, "Create 6.0.0", {"create"})
    assert manager.db.get_setting(fit.SETTING) is None, "an empty card is cleared"


def test_mods_list_carries_the_card_and_hide_clears_it(fabric):  # noqa: F811
    client, headers, _ = fabric
    core = client.app.state.core
    card = {
        **fit.empty("Create 6.0"),
        "needs_change": [
            {
                "mod_id": "r",
                "name": "R",
                "filename": "r.jar",
                "why": "w",
                "from": "1",
                "to": "2",
                "version_id": "R2",
            }
        ],
    }
    core.mods.db.set_setting(fit.SETTING, card)

    listed = client.get("/api/mods", headers=headers).json()
    assert listed["fit"]["needs_change"][0]["from"] == "1"
    assert client.post("/api/mods/fit/hide", headers=headers).json() == {"ok": True}
    assert client.get("/api/mods", headers=headers).json()["fit"] is None


# ---------------------------------------------------------------- Modrinth's environment
@pytest.mark.parametrize(
    "data, client_side, server_side",
    [
        ({"environment": ["client_and_server"]}, "required", "required"),
        ({"environment": ["client_only"], "server_side": "required"}, "required", "unsupported"),
        ({"environment": ["server_only", "dedicated_server_only"]}, "unsupported", "required"),
        ({"environment": ["client_or_server"]}, "optional", "optional"),
        ({"environment": "server_only_client_optional"}, "optional", "required"),
        # Nothing said in environment: the old fields while Modrinth still sends them.
        (
            {"environment": ["unknown"], "client_side": "optional", "server_side": "required"},
            "optional",
            "required",
        ),
        ({}, None, None),
    ],
)
def test_sides_come_from_environment(data, client_side, server_side):
    assert sides(data) == {"client_side": client_side, "server_side": server_side}
