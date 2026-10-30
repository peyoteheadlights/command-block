"""Keeping current without a release: saved copies of every official list,
the Java a version needs read from Mojang's own file, the signed notes file
(safety warnings, known bad files, extra Java rows) and the daily look for
newer server software.

Every official site is a fake (the safe downloader's swappable transport),
and the notes file is signed with a key made in the test: the key built
into the app is a test key nobody holds, so a real agent refuses every
notes file until the real one is made."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from pathlib import Path

import httpx
import pytest

from agent import downloads, notes, savedlists, signing
from agent.core import AgentCore
from agent.minecraft import java
from agent.mods.manager import ModError
from agent.servertypes import versions as versions_module

from .conftest import build_multi_config
from .test_mods import make_jar, manager, modrinth_with_transport  # noqa: F401
from .test_server_types import MC, fake_sources

DAY = 86400.0
# What the fake Mojang version files say, so an answer can be told apart
# from the built-in table's (which says 21 for 1.21.1 and nothing for a
# snapshot).
MOJANG_JAVA = {MC: 22, "24w40a": 23}


def sources(down: tuple[str, ...] = (), broken: tuple[str, ...] = (), **kwargs):
    base = fake_sources(**kwargs)

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host in down:
            raise httpx.ConnectError("the site is down", request=request)
        if host in broken:
            return httpx.Response(200, text="<html>Oops</html>")
        response = base(request)
        name = path.rsplit("/", 1)[-1].removesuffix(".json")
        if host == "piston-meta.mojang.com" and name in MOJANG_JAVA:
            data = json.loads(response.content)
            data["javaVersion"] = {"component": "java-runtime", "majorVersion": MOJANG_JAVA[name]}
            return httpx.Response(200, json=data)
        return response

    return handler


@pytest.fixture
def net(monkeypatch):
    def use(**kwargs):
        monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(sources(**kwargs)))

    use()
    return use


@pytest.fixture
async def core(tmp_path, net):
    agent = AgentCore(build_multi_config(tmp_path))
    yield agent
    await agent.jobs.stop()
    agent.db.close()


def paper_versions(client) -> httpx.Response:
    return client.get("/api/server-types/paper/versions")


# ---------------------------------------------------------------- saved copies
def test_the_picker_shows_the_saved_list_with_its_age_when_the_site_is_down(
    multi_client, net, monkeypatch
):
    live = paper_versions(multi_client).json()
    assert live["source_problem"] is None and live["saved_at"] is None
    net(down=("fill.papermc.io",))
    later = time.time() + 3 * DAY
    monkeypatch.setattr(
        savedlists, "time", type("Clock", (), {"time": staticmethod(lambda: later)})
    )
    saved = paper_versions(multi_client).json()
    assert [v["minecraft"] for v in saved["versions"]] == [v["minecraft"] for v in live["versions"]]
    assert saved["source_problem"] == "From 3 days ago, couldn't reach PaperMC."
    assert later - saved["saved_at"] >= 3 * DAY


def test_with_no_saved_copy_the_picker_says_it_couldnt_check(multi_client, net):
    net(down=("fill.papermc.io",))
    response = paper_versions(multi_client)
    assert response.status_code >= 400
    assert "Couldn't reach fill.papermc.io" in response.text
    assert "versions" not in response.json()


def test_a_broken_answer_never_replaces_a_good_copy(multi_client, net):
    good = paper_versions(multi_client).json()
    net(broken=("fill.papermc.io",))
    after = paper_versions(multi_client).json()
    assert after["versions"] == good["versions"]
    assert after["source_problem"].endswith("couldn't reach PaperMC.")
    net(down=("fill.papermc.io",))
    assert paper_versions(multi_client).json()["versions"] == good["versions"]


def test_loader_and_build_lists_fall_back_too(multi_client, net):
    for type_id in ("fabric", "forge", "neoforge", "purpur", "vanilla"):
        live = multi_client.get(f"/api/server-types/{type_id}/versions").json()
        net(
            down=(
                "meta.fabricmc.net",
                "maven.minecraftforge.net",
                "maven.neoforged.net",
                "api.purpurmc.org",
                "piston-meta.mojang.com",
            )
        )
        saved = multi_client.get(f"/api/server-types/{type_id}/versions").json()
        assert saved["versions"] == live["versions"], type_id
        assert saved["source_problem"].startswith("From "), type_id
        net()


async def test_a_plan_from_a_saved_list_still_downloads_and_checks_the_file(core, net, tmp_path):
    await versions_module.make_plan("paper", MC)  # read once, so it is saved
    net(down=("fill.papermc.io",))
    plan = await versions_module.make_plan("paper", MC)
    spec = plan.downloads[0].spec
    assert spec.url.startswith("https://fill-data.papermc.io/")
    fetched = await downloads.download(spec, tmp_path)
    assert fetched.checked_with == "sha256"
    # A file that doesn't match its saved checksum is still thrown away.
    net(paper_sha="0" * 64)
    await versions_module.make_plan("paper", MC)
    net(down=("fill.papermc.io",))
    plan = await versions_module.make_plan("paper", MC)
    with pytest.raises(downloads.DownloadError, match="didn't match"):
        await downloads.download(plan.downloads[0].spec, tmp_path / "again")


def test_bedrocks_kept_copies_still_answer_as_before(multi_client):
    answer = multi_client.get("/api/server-types/bedrock/versions")
    # Nothing kept and Mojang unreachable in this fake: the same plain error.
    assert answer.status_code >= 400


# ---------------------------------------------------------------- Modrinth lists
def modrinth_answer(version: str) -> list[dict]:
    return [
        {
            "id": f"v{version}",
            "project_id": "ALPHA",
            "version_number": version,
            "version_type": "release",
            "game_versions": ["1.20.1"],
            "loaders": ["fabric"],
            "files": [
                {
                    "primary": True,
                    "filename": f"alpha-{version}.jar",
                    "url": f"https://cdn.modrinth.com/data/A/versions/{version}/alpha.jar",
                    "hashes": {"sha512": "a" * 128},
                }
            ],
        }
    ]


async def test_update_checks_use_modrinths_saved_list_and_never_call_it_up_to_date(
    manager,  # noqa: F811
    config,
):
    make_jar(config.mods_dir / "alpha.jar", "alpha", version="1.0", name="Alpha")
    up = {"answer": modrinth_answer("2.0"), "down": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if up["down"]:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json=up["answer"])

    manager.modrinth = modrinth_with_transport(config, handler)
    first = await manager.update_plan()
    assert [u["latest_version"] for u in first["updates"]] == ["2.0"]
    assert first["updates"][0]["saved"] is None

    up["down"] = True
    saved = await manager.update_plan()
    assert [u["latest_version"] for u in saved["updates"]] == ["2.0"]
    assert saved["updates"][0]["saved"].endswith("couldn't reach Modrinth.")

    # A saved list showing nothing newer is "not checked", never "up to date".
    up.update(down=False, answer=modrinth_answer("1.0"))
    await manager.update_plan()
    up["down"] = True
    plan = await manager.update_plan()
    assert plan["updates"] == []
    assert "couldn't reach Modrinth" in plan["unchecked"][0]["reason"]


# ---------------------------------------------------------------- Java from Mojang
async def test_picking_a_version_reads_the_java_mojang_asks_for(core):
    assert java.required_java(MC) == 21  # the table, before Mojang was asked
    assert await versions_module.java_for(MC) == 22
    assert java.required_java(MC) == 22
    # The start check and the "Java too old" Recommendation use it.
    ctx = core.servers["survival"]
    ctx.server.java_info = java.JavaInfo(version_major=21)
    ctx.server.mc_version = MC
    assert ctx.server.java_compatibility()["verdict"] == "incompatible"
    assert ctx.server.java_compatibility()["required"] == 22


async def test_snapshots_get_their_java_from_mojang(core):
    assert java.required_java("24w40a") is None
    assert await versions_module.java_for("24w40a") == 23
    listed = await versions_module.report("vanilla")
    snapshot = next(v for v in listed["versions"] if v["minecraft"] == "24w40a")
    assert snapshot["java_required"] == 23


async def test_the_answer_is_kept_for_the_next_start(core, tmp_path):
    await versions_module.java_for(MC)
    java.FROM_MOJANG.clear()
    versions_module.load_java()
    assert java.required_java(MC) == 22


async def test_mojang_unreachable_and_nothing_saved_falls_back_to_the_table(core, net):
    net(down=("piston-meta.mojang.com",))
    assert await versions_module.java_for(MC) == 21
    assert await versions_module.java_for("24w40a") is None  # no guess


async def test_mojang_down_uses_its_saved_file(core, net):
    await versions_module.java_for("24w40a")
    java.FROM_MOJANG.clear()
    net(down=("piston-meta.mojang.com",))
    assert await versions_module.java_for("24w40a") == 23


# ---------------------------------------------------------------- the notes loader
NOW = dt.datetime(2026, 11, 1, tzinfo=dt.UTC).timestamp()
BAD_SHA = hashlib.sha512(b"an infected jar").hexdigest()


def notes_body(**overrides) -> dict:
    body = {
        "schema": 1,
        "serial": 5,
        "issued": "2026-10-30",
        "expires": "2026-11-29",
        "advisories": [
            {
                "id": "log4shell",
                "types": ["paper", "fabric"],
                "from": "1.17",
                "to": "1.21.9",
                "title": "Update to fix a security hole",
                "what_to_do": "Change to the newest build.",
                "link": "https://example.org/advisory",
            }
        ],
        "known_bad_files": [{"sha512": BAD_SHA, "name": "Bad mod", "why": "It steals accounts"}],
        "java": [{"minecraft": "27.1", "java": 27}],
    }
    body.update(overrides)
    return body


@pytest.fixture
def key(monkeypatch):
    private, public = signing.new_key()
    monkeypatch.setattr(notes, "DATA_PUBLIC_KEY", public)
    return private


def signed(body: dict | bytes, private: str) -> tuple[bytes, str]:
    document = body if isinstance(body, bytes) else json.dumps(body).encode()
    return document, signing.sign(document, private)


def load(body, private, highest=0, now=NOW, app="0.9.0", keys=None) -> notes.Notes:
    document, signature = signed(body, private)
    return notes.check(document, signature, keys or [notes.DATA_PUBLIC_KEY], highest, now, app)


def test_a_good_notes_file_is_read(key):
    found = load(notes_body(), key)
    assert (found.serial, found.expired, found.skipped) == (5, False, 0)
    assert found.advisories[0]["id"] == "log4shell"
    assert found.known_bad_files[0]["sha512"] == BAD_SHA


def test_a_file_signed_with_another_key_is_refused(key):
    other, _ = signing.new_key()
    with pytest.raises(notes.NotesRefused, match="isn't signed"):
        load(notes_body(), other)


def test_the_built_in_test_key_refuses_every_file():
    private, _ = signing.new_key()
    document, signature = signed(notes_body(), private)
    with pytest.raises(notes.NotesRefused, match="isn't signed"):
        notes.check(
            document, signature, [notes.DATA_PUBLIC_KEY, notes.DATA_NEXT_KEY], 0, NOW, "0.9.0"
        )


def test_the_next_key_slot_is_accepted(key):
    nxt, public = signing.new_key()
    assert load(notes_body(), nxt, keys=["", public]).serial == 5


def test_an_older_serial_is_refused(key):
    with pytest.raises(notes.NotesRefused, match="older"):
        load(notes_body(serial=4), key, highest=5)
    assert load(notes_body(serial=5), key, highest=5).serial == 5


def test_an_expired_file_keeps_its_warnings(key):
    found = load(notes_body(), key, now=NOW + 40 * DAY)
    assert found.expired and found.advisories


def test_a_file_over_256_kb_is_refused(key):
    big = json.dumps(notes_body(padding="x" * 300_000)).encode()
    with pytest.raises(notes.NotesRefused, match="too big"):
        load(big, key)


@pytest.mark.parametrize(
    "change",
    [
        {"serial": "5"},
        {"expires": 20261129},
        {"advisories": {"id": "x"}},
        {"known_bad_files": [{"sha512": "short", "name": "x", "why": "y"}]},
        {"java": [{"minecraft": "27.1", "java": "27"}]},
        {"advisories": [{**notes_body()["advisories"][0], "title": "<b>bold</b>"}]},
        {"advisories": [{**notes_body()["advisories"][0], "types": ["spigot"]}]},
        {"schema": 2},
    ],
)
def test_a_wrong_type_anywhere_refuses_the_whole_file(key, change):
    with pytest.raises(notes.NotesRefused):
        load(notes_body(**change), key)


def test_a_link_that_isnt_https_is_refused(key):
    advisory = {**notes_body()["advisories"][0], "link": "http://example.org/x"}
    with pytest.raises(notes.NotesRefused, match="https"):
        load(notes_body(advisories=[advisory]), key)


def test_unknown_keys_are_ignored(key):
    advisory = {**notes_body()["advisories"][0], "severity": "high"}
    found = load(notes_body(advisories=[advisory], recommended_addons=[{"x": 1}]), key)
    assert found.advisories[0]["id"] == "log4shell"


def test_entries_for_a_newer_app_are_skipped(key):
    advisory = {**notes_body()["advisories"][0], "min_app": "1.2.0"}
    found = load(notes_body(advisories=[advisory]), key)
    assert found.advisories == [] and found.skipped == 1


def test_the_repos_own_notes_file_passes_the_same_loader():
    document = (Path(__file__).parent.parent / "data" / "catalog.json").read_bytes()
    body = json.loads(document)
    issued = dt.datetime.fromisoformat(body["issued"]).replace(tzinfo=dt.UTC)
    found = notes.parse(document, 0, issued.timestamp(), "0.9.0")
    assert found.serial >= 1


# ---------------------------------------------------------------- the notes, on the agent
def serve_notes(net_use, monkeypatch, document: bytes, signature: str, seen: list | None = None):
    base = sources()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com" and request.url.path.endswith("catalog.json"):
            if seen is not None:
                seen.append(request.headers.get("if-none-match"))
            if request.headers.get("if-none-match") == '"v1"':
                return httpx.Response(304)
            return httpx.Response(200, content=document, headers={"etag": '"v1"'})
        if request.url.host == "github.com" and request.url.path.endswith("catalog.json.sig"):
            return httpx.Response(200, text=signature)
        return base(request)

    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(handler))


def test_with_no_good_notes_file_the_status_says_it_couldnt_check(multi_client):
    status = multi_client.get("/api/safety-notes").json()
    assert status["state"] == "none"
    assert status["message"] == "Couldn't check for safety notes."
    assert "no problems" not in json.dumps(status).lower()


async def test_a_signed_notes_file_is_fetched_kept_and_reported(core, key, monkeypatch, net):
    seen: list = []
    serve_notes(net, monkeypatch, *signed(notes_body(), key), seen=seen)
    status = await core.notes.check_now()
    assert status["state"] == "good" and status["serial"] == 5
    assert core.db.get_setting(notes.SERIAL_KEY) == 5
    # Asked again with its ETag: unchanged, nothing fetched twice.
    await core.notes.check_now()
    assert seen == [None, '"v1"']
    # A newer agent start reads the kept copy.
    again = notes.SafetyNotes(core)
    assert again.status()["state"] == "good"


async def test_a_refused_file_keeps_the_last_good_one(core, key, monkeypatch, net):
    serve_notes(net, monkeypatch, *signed(notes_body(), key))
    await core.notes.check_now()
    other, _ = signing.new_key()
    serve_notes(net, monkeypatch, *signed(notes_body(serial=6), other))
    core.notes.notes = core.notes.notes  # the good one stays
    (core.notes.folder / "meta.json").write_text("{}")  # no ETag: fetched again
    status = await core.notes.check_now()
    assert status["state"] == "good" and status["serial"] == 5
    assert "isn't signed" in status["reason"]


async def test_an_expired_file_is_labelled_with_its_age(core, key, monkeypatch, net):
    body = notes_body(issued="2026-09-01", expires="2026-10-01")
    serve_notes(net, monkeypatch, *signed(body, key))
    await core.notes.check_now()
    assert core.notes.status()["state"] == "expired"
    assert core.notes.status()["message"].startswith("Notes from ")
    assert core.notes.status()["message"].endswith("couldn't get newer ones.")


async def test_a_file_with_entries_for_a_newer_app_says_to_update(core, key, monkeypatch, net):
    advisory = {**notes_body()["advisories"][0], "min_app": "9.0.0"}
    serve_notes(net, monkeypatch, *signed(notes_body(advisories=[advisory]), key))
    status = await core.notes.check_now()
    assert status["update_app"] == "Update Command Block to get the newest notes."


# ---------------------------------------------------------------- warnings and Java rows
async def test_a_matching_warning_becomes_a_recommendation_with_a_link_only(
    core, key, monkeypatch, net
):
    from agent import recommendations

    serve_notes(net, monkeypatch, *signed(notes_body(), key))
    await core.notes.check_now()
    ctx = core.servers["survival"]  # a Fabric server
    ctx.server.mc_version = None
    assert not [
        r
        for r in recommendations.evaluate(recommendations.gather(ctx))
        if r.id.startswith("advisory_")
    ]
    ctx.server.mc_version = MC
    found = {r.id: r for r in recommendations.evaluate(recommendations.gather(ctx))}
    advisory = found["advisory_log4shell"]
    assert advisory.action == {"label": "Read more", "url": "https://example.org/advisory"}
    assert "affected range" in advisory.evidence
    # Dismissing one warning hides only that one.
    recommendations.act(ctx.db, list(found.values()), "advisory_log4shell", "dismiss")
    shown = recommendations.visible(ctx.db, list(found.values()))
    assert "advisory_log4shell" not in [r["id"] for r in shown["recommendations"]]


async def test_java_rows_from_the_notes_only_add_newer_versions(core, key, monkeypatch, net):
    rows = [{"minecraft": "27.1", "java": 27}, {"minecraft": "1.21", "java": 8}]
    serve_notes(net, monkeypatch, *signed(notes_body(java=rows), key))
    await core.notes.check_now()
    assert java.required_java("27.1") == 27
    assert java.required_java("1.21") == 21  # can't lower the table


# ---------------------------------------------------------------- known bad files
async def test_a_known_bad_file_is_refused_and_one_already_there_is_flagged(
    core, key, monkeypatch, net
):
    from agent import recommendations

    ctx = core.servers["survival"]
    (ctx.config.server_dir / "libraries").mkdir(exist_ok=True)
    good = make_jar(ctx.config.mods_dir / "good.jar", "good")
    bad_bytes = make_jar(Path(ctx.config.server_dir) / "bad.jar", "bad").read_bytes()
    body = notes_body(
        known_bad_files=[
            {
                "sha512": hashlib.sha512(bad_bytes).hexdigest(),
                "name": "Bad",
                "why": "It steals accounts",
            }
        ]
    )
    # Before any notes file, installs work as today.
    (ctx.config.mods_dir / "already.jar").write_bytes(bad_bytes)
    serve_notes(net, monkeypatch, *signed(body, key))
    await core.notes.check_now()
    with pytest.raises(ModError, match="known bad files"):
        await ctx.mods.install_local_file("bad2.jar", bad_bytes, user="owner")
    assert not (ctx.config.mods_dir / "bad2.jar").exists()
    await ctx.mods.install_local_file(
        "fine.jar", good.read_bytes(), user="owner", allow_replace=True
    )
    found = {r.id: r for r in recommendations.evaluate(recommendations.gather(ctx))}
    assert found["known_bad_files"].evidence.endswith("already.jar.")


# ---------------------------------------------------------------- newer server software
async def test_a_newer_build_and_a_newer_minecraft_are_offered_once(core, net):
    from agent import recommendations

    ctx = core.servers["survival"]
    alerts = []
    core.bus.subscribe(
        lambda e: alerts.append(e) if e.type == "server_software_available" else None
    )
    monkey_type(ctx, "paper")
    ctx.server.mc_version, ctx.server.loader_version = "1.21", "1.21-100"
    offers = await core.software.check_server(ctx)
    assert offers["minecraft"] == {"from": "1.21", "minecraft": MC}
    found = {r.id: r for r in recommendations.evaluate(recommendations.gather(ctx))}
    assert found["newer_minecraft"].details["fix"]["minecraft_version"] == MC
    # On 1.21.1 build 100: Paper's newest stable build there is 129.
    ctx.server.mc_version, ctx.server.loader_version = MC, f"{MC}-100-main@abc"
    offers = await core.software.check_server(ctx)
    assert offers["build"]["build"] == "129" and offers["minecraft"] is None
    found = {r.id: r for r in recommendations.evaluate(recommendations.gather(ctx))}
    assert found["newer_build"].fingerprint == "build:129"
    await core.software.check_server(ctx)  # the same offer again: no second alert
    await asyncio_settle()
    sent = [(a.data["kind"], a.data["minecraft"], a.data.get("build")) for a in alerts]
    assert sorted(sent) == [("build", "1.21", "129"), ("build", MC, "129"), ("minecraft", MC, None)]
    # The installed version shown stays the console's.
    assert ctx.server.status()["minecraft_version"] == MC


async def test_nothing_is_offered_from_a_saved_list(core, net):
    ctx = core.servers["survival"]
    monkey_type(ctx, "paper")
    ctx.server.mc_version, ctx.server.loader_version = "1.21", "1.21-100"
    await versions_module.list_versions("paper")  # saved
    net(down=("fill.papermc.io",))
    offers = await core.software.check_server(ctx)
    assert not offers.get("minecraft")


async def test_an_unknown_version_offers_nothing(core):
    ctx = core.servers["survival"]
    assert await core.software.check_server(ctx) == {}


async def test_unstable_builds_are_never_offered(core, net, monkeypatch):
    ctx = core.servers["survival"]
    monkey_type(ctx, "fabric")
    ctx.server.mc_version, ctx.server.loader_version = MC, "0.16.5"
    offers = await core.software.check_server(ctx)
    # Fabric's 0.16.6 is marked unstable in the fake.
    assert offers.get("build") is None


# ---------------------------------------------------------------- Bedrock by itself
async def test_bedrock_updates_by_itself_once_nobody_is_online(core, monkeypatch):
    from agent import softwarecheck
    from agent.servertypes import bedrock, install

    ctx = core.servers["survival"]
    calls = []

    async def newest():
        return "1.21.50.1", "https://www.minecraft.net/bedrockdedicatedserver/x.zip"

    async def change(ctx_, type_id, minecraft, **kwargs):
        calls.append((type_id, minecraft, ctx_.players.online_count()))
        return {"ok": True}

    monkeypatch.setattr(bedrock, "latest", newest)
    monkeypatch.setattr(install, "change_version", change)
    monkeypatch.setattr(softwarecheck, "WAIT_FOR_EMPTY", 0.01)
    ctx.server.mc_version = "1.21.40.3"
    assert await core.software.update_bedrock(ctx) is None  # off by default
    ctx.config.set("server.bedrock_auto_update", True)
    online = {"count": 2}
    monkeypatch.setattr(type(ctx.server), "running", property(lambda self: True))
    monkeypatch.setattr(ctx.players, "online_count", lambda: online["count"])

    import asyncio

    task = asyncio.create_task(core.software.update_bedrock(ctx))
    await asyncio.sleep(0.05)
    assert calls == []  # players online: waits
    online["count"] = 0
    assert await asyncio.wait_for(task, 2) == {"ok": True}
    assert calls == [("bedrock", "1.21.50.1", 0)]


def test_only_the_owner_can_switch_bedrock_updates(multi_client):
    answer = multi_client.put(
        "/api/servers/survival/settings", json={"updates": {"server.bedrock_auto_update": True}}
    ).json()
    assert answer["applied"] == {"server.bedrock_auto_update": True}


# ---------------------------------------------------------------- helpers
def monkey_type(ctx, type_id: str) -> None:
    ctx.config.set("server.type", type_id)


async def asyncio_settle() -> None:
    import asyncio

    for _ in range(5):
        await asyncio.sleep(0)
