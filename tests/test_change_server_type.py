"""Change server type, and Bedrock to Java (or back) as a new server.

The official sources are the fake network from test_server_types.py, so the
real version change routine runs: backup, download, check, Roll back. The
checks look at the server folder afterwards: where the world's Nether and
End went, what moved to a "Not used by <type>" folder, and that nothing was
deleted. Chunker is a fake that records how it was started and writes a
world, the way the Forge installer's fake does.
"""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import pytest

from agent import notes, servertypes, signing
from agent.security import permissions as P
from agent.security.auth import Access, Principal
from agent.servertypes import change, chunker
from agent.servertypes import create as create_module
from agent.servertypes import install as install_module
from agent.servertypes.install import InstallError

from . import test_server_types
from .test_keeping_current import NOW, notes_body, signed
from .test_mods import make_jar
from .test_server_types import MC

# The fake official sources, agent and Forge installer from test_server_types.py.
network = test_server_types.network
core = test_server_types.core
ctx = test_server_types.ctx
fake_installer = test_server_types.fake_installer

TYPES = servertypes.TYPES


def plugin_jar(path: Path, name: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("plugin.yml", f"name: {name}\nversion: 1.0\nmain: a.Main\n")
    return path


def every_jar(folder: Path) -> list[str]:
    """The add-ons in the add-on folders and any "Not used by" folder."""
    places = [folder / "mods", folder / "plugins", *folder.glob("Not used by *")]
    return sorted(p.name for place in places for p in place.glob("*.jar*"))


def make_world(folder: Path, split: bool) -> None:
    """A world with a Nether and an End, in Paper's layout or everyone else's."""
    (folder / "world").mkdir(parents=True, exist_ok=True)
    (folder / "world" / "level.dat").write_bytes(b"level")
    for dim, suffix in (("DIM-1", "_nether"), ("DIM1", "_the_end")):
        place = folder / f"world{suffix}" / dim if split else folder / "world" / dim
        place.mkdir(parents=True)
        (place / "r.0.0.mca").write_bytes(dim.encode())


@pytest.fixture
def seen(ctx):
    """The console printed the version, so the change knows what to keep."""
    ctx.server.mc_version = MC
    return ctx


# ---------------------------------------------------------------- what's offered
def test_simple_mode_offers_only_vanilla_to_and_from_fabric_and_paper():
    ids = lambda kind, technical: [t.id for t in change.targets(TYPES[kind], technical)]  # noqa: E731
    assert ids("vanilla", False) == ["fabric", "paper"]
    assert ids("fabric", False) == ["vanilla"]
    assert ids("paper", False) == ["vanilla"]
    assert ids("forge", False) == []


def test_technical_mode_offers_every_java_type_to_every_other():
    for kind in ("vanilla", "fabric", "quilt", "forge", "neoforge", "paper", "purpur", "leaf"):
        found = {t.id for t in change.targets(TYPES[kind], True)}
        java = {t.id for t in TYPES.values() if t.edition == "java"}
        assert found == java - {kind}, kind
    assert change.targets(TYPES["bedrock"], True) == []


def test_a_pair_simple_mode_doesnt_offer_is_refused_with_the_reason():
    with pytest.raises(InstallError, match="only offered in Technical mode"):
        change.check_target(TYPES["fabric"], "forge", technical=False)
    with pytest.raises(InstallError, match="stored differently"):
        change.check_target(TYPES["fabric"], "bedrock", technical=True)


def test_a_moderator_never_gets_change_server_type():
    """Only the owner: the routes need SERVERS_MANAGE, which no moderator can
    be given, and a type change through the version route checks it too."""
    from agent.api.routes import api_routes
    from agent.api.routes import servertypes as routes

    everything = frozenset(P.SERVER_PERMISSIONS)
    moderator = Principal(
        user="sam",
        kind="session",
        role=P.HELPER,
        access=Access(grants={"survival": everything}, all_servers=False, new_server=frozenset()),
    )
    with pytest.raises(P.AuthError):
        P.check(moderator, P.SERVERS_MANAGE, "survival")
    declared = [
        [d.call.permission for d in r.dependant.dependencies if hasattr(d.call, "permission")]
        for path, r, _ in api_routes()
        if "/type-change" in path
    ]
    assert len(declared) == 5 and all(p == [P.SERVERS_MANAGE] for p in declared)
    import inspect

    assert "check(principal, SERVERS_MANAGE" in inspect.getsource(routes.change_version)


# ---------------------------------------------------------------- Vanilla <-> Fabric, Paper
async def test_vanilla_to_paper_keeps_the_version_and_moves_the_nether_and_end(seen, network):
    ctx = seen
    folder = ctx.config.server_dir
    ctx.config.set("server.type", "vanilla")
    (folder / "server.jar").write_bytes(b"the vanilla jar")
    make_world(folder, split=False)

    plan = await change.plan(ctx, "paper", technical=False)
    assert plan["minecraft_version"] == MC and plan["target"]["minecraft_version"] == MC
    assert plan["files"]["dimensions"] == [
        ["world/DIM-1", "world_nether/DIM-1"],
        ["world/DIM1", "world_the_end/DIM1"],
    ]
    result = await change.change_type(ctx, "paper", technical=False, user="owner")

    assert ctx.config.server.type == "paper"
    assert (folder / "world_nether" / "DIM-1" / "r.0.0.mca").read_bytes() == b"DIM-1"
    assert (folder / "world_the_end" / "DIM1" / "r.0.0.mca").read_bytes() == b"DIM1"
    assert not (folder / "world" / "DIM-1").exists()
    # A verified backup first; the version waits for the console (house rule 1).
    assert result["safety_backup"]["verified"] is True
    assert ctx.server.mc_version is None
    assert ctx.server.status()["pending_version"]["minecraft_version"] == MC

    # The one way back puts Vanilla and the old layout back.
    await install_module.roll_back(ctx, user="owner")
    assert ctx.config.server.type == "vanilla"
    assert (folder / "server.jar").read_bytes() == b"the vanilla jar"
    assert (folder / "world" / "DIM-1" / "r.0.0.mca").read_bytes() == b"DIM-1"
    assert not (folder / "world_nether" / "DIM-1").exists()


async def test_paper_to_vanilla_moves_the_nether_and_end_back_inside(seen, network):
    ctx = seen
    folder = ctx.config.server_dir
    ctx.config.set("server.type", "paper")
    plugin_jar(folder / "plugins" / "essentials.jar", "Essentials")
    make_world(folder, split=True)
    await change.change_type(ctx, "vanilla", technical=False, user="owner")
    assert (folder / "world" / "DIM-1" / "r.0.0.mca").read_bytes() == b"DIM-1"
    assert (folder / "world" / "DIM1" / "r.0.0.mca").read_bytes() == b"DIM1"
    # Vanilla ignores plugins/, so it stays where it is.
    assert (folder / "plugins" / "essentials.jar").is_file()


async def test_vanilla_to_fabric_and_back(seen, network):
    ctx = seen
    ctx.config.set("server.type", "vanilla")
    await change.change_type(ctx, "fabric", technical=False, user="owner")
    assert ctx.config.server.type == "fabric"
    assert (ctx.config.server_dir / "fabric-server-launch.jar").is_file()
    ctx.server.mc_version = MC  # the console printed it after a start
    await change.change_type(ctx, "vanilla", technical=False, user="owner")
    assert ctx.config.server.type == "vanilla"


async def test_a_type_with_no_build_for_the_version_is_refused(ctx, network):
    ctx.config.set("server.type", "vanilla")
    ctx.server.mc_version = "1.20.1"  # Paper's fake list has only 1.21.x
    with pytest.raises(InstallError, match="Paper has no build for Minecraft 1.20.1"):
        await change.change_type(ctx, "paper", technical=False, user="owner")
    assert ctx.config.server.type == "vanilla"


async def test_an_unseen_version_is_never_guessed(ctx, network):
    with pytest.raises(InstallError, match="Start the server once"):
        await change.change_type(ctx, "vanilla", technical=False)
    assert change.options(ctx, False)["minecraft_version"] is None


async def test_a_running_server_needs_stop_and_change(seen, network, monkeypatch):
    monkeypatch.setattr(type(seen.server), "running", property(lambda self: True))
    with pytest.raises(InstallError, match="Stop and change"):
        await change.change_type(seen, "vanilla", technical=False)
    assert change.options(seen, False)["running"] is True


async def test_the_version_route_keeps_the_version_on_a_type_change(seen, network):
    with pytest.raises(InstallError, match="keeps Minecraft"):
        await change.change_type(seen, "vanilla", technical=True, minecraft="1.20.1")


# ---------------------------------------------------------------- every pair, Not used by
async def test_mods_to_plugins_and_back_leaves_each_folder_alone(seen, network):
    ctx = seen
    folder = ctx.config.server_dir
    make_jar(folder / "mods" / "lithium.jar", "lithium")
    await change.change_type(ctx, "paper", technical=True, user="owner")
    plugin_jar(folder / "plugins" / "luckperms.jar", "LuckPerms")
    assert ctx.config.mods_dir.name == "plugins"
    ctx.server.mc_version = MC
    await change.change_type(ctx, "fabric", technical=True, user="owner")
    assert (folder / "mods" / "lithium.jar").is_file()
    assert (folder / "plugins" / "luckperms.jar").is_file()
    assert not list(folder.glob("Not used by *"))


async def test_add_ons_that_cant_come_along_move_aside_and_come_back(
    seen,
    network,
    fake_installer,
):
    ctx = seen
    folder = ctx.config.server_dir
    make_jar(folder / "mods" / "sodium.jar", "sodium")
    make_jar(folder / "mods" / "jei.jar", "jei", loader="forge")
    make_jar(folder / "mods" / "mystery.jar", "mystery", loader="none")  # says nothing
    before = every_jar(folder)

    plan = await change.plan(ctx, "forge", technical=True)
    assert plan["moved_aside"] == ["mystery.jar", "sodium.jar"]
    assert plan["files"]["kept"] == ["jei.jar"]
    await change.change_type(ctx, "forge", technical=True, user="owner")
    aside = folder / "Not used by Forge"
    assert sorted(p.name for p in aside.iterdir()) == ["mystery.jar", "sodium.jar"]
    assert every_jar(folder) == before, "nothing is deleted"

    ctx.server.mc_version = MC
    await change.change_type(ctx, "fabric", technical=True, user="owner")
    assert sorted(p.name for p in (folder / "mods").iterdir()) == ["sodium.jar"]
    assert [p.name for p in (folder / "Not used by Fabric").iterdir()] == ["jei.jar"]
    assert (aside / "mystery.jar").is_file(), "no loader says it fits, so it stays aside"
    assert every_jar(folder) == before, "nothing is deleted"


async def test_roll_back_puts_moved_add_ons_back(seen, network, fake_installer):
    ctx = seen
    folder = ctx.config.server_dir
    make_jar(folder / "mods" / "sodium.jar", "sodium")
    await change.change_type(ctx, "neoforge", technical=True, user="owner")
    assert not (folder / "mods" / "sodium.jar").exists()
    await install_module.roll_back(ctx, user="owner")
    assert (folder / "mods" / "sodium.jar").is_file()
    assert not list(folder.glob("Not used by *"))


async def test_two_nethers_are_never_merged_or_overwritten(seen, network):
    ctx = seen
    folder = ctx.config.server_dir
    ctx.config.set("server.type", "vanilla")
    make_world(folder, split=False)
    (folder / "world_nether" / "DIM-1").mkdir(parents=True)
    with pytest.raises(InstallError, match="can't tell which one to keep"):
        await change.change_type(ctx, "paper", technical=False)
    assert ctx.config.server.type == "vanilla"
    assert (folder / "world" / "DIM-1" / "r.0.0.mca").is_file()


async def test_a_custom_jar_changes_like_its_base_type(seen, network):
    ctx = seen
    folder = ctx.config.server_dir
    ctx.config.set("server.type", "paper")
    ctx.config.set("server.custom_jar", True)
    ctx.config.set("server.jar", "my-fork.jar")
    (folder / "my-fork.jar").write_bytes(b"my own jar")
    assert [t.id for t in change.targets(ctx.config.server_type, False)] == ["vanilla"]
    await change.change_type(ctx, "vanilla", technical=False, user="owner")
    assert ctx.config.server.custom_jar is False
    assert not (folder / "my-fork.jar").exists()
    await install_module.roll_back(ctx, user="owner")
    assert ctx.config.server.custom_jar is True
    assert (folder / "my-fork.jar").read_bytes() == b"my own jar"


# ---------------------------------------------------------------- similar add-ons
LOOK = {
    "source": "modrinth",
    "project": "simple-voice-chat",
    "type": "paper",
    "look_alikes": [{"project": "hangar:42", "name": "Voice Chat for Paper"}],
}


def test_look_alikes_come_only_from_a_signed_notes_file(monkeypatch):
    monkeypatch.setattr(notes, "LOOK_ALIKES", {})
    private, public = signing.new_key()
    document, signature = signed(notes_body(look_alikes=[LOOK]), private)
    found = notes.check(document, signature, [public], 0, NOW, "0.9.0")
    holder = notes.SafetyNotes.__new__(notes.SafetyNotes)
    holder._apply(found)
    assert notes.look_alikes("modrinth", "simple-voice-chat", "paper") == LOOK["look_alikes"]
    assert notes.look_alikes("modrinth", "simple-voice-chat", "fabric") == []


@pytest.mark.parametrize(
    "bad",
    [
        {"project": "owner/repo/1"},  # a GitHub file has no project
        {"project": "../x"},
        {"type": "bedrock"},
        {"type": "spigot"},
        {"look_alikes": []},
        {"look_alikes": [{"project": "x", "name": "<b>"}]},
        {"look_alikes": [{"project": "x", "name": "y"}] * 11},
    ],
)
def test_a_bad_look_alike_refuses_the_whole_file(bad):
    private, public = signing.new_key()
    document, signature = signed(notes_body(look_alikes=[{**LOOK, **bad}]), private)
    with pytest.raises(notes.NotesRefused):
        notes.check(document, signature, [public], 0, NOW, "0.9.0")


async def test_similar_add_ons_are_the_same_project_first_then_look_alikes(
    seen,
    network,
    monkeypatch,
):
    ctx = seen
    folder = ctx.config.server_dir
    for mod_id in ("spark", "simple-voice-chat", "handmade"):
        make_jar(folder / "mods" / f"{mod_id}.jar", mod_id)
    ctx.mods.remember("spark", "modrinth", {"project_id": "spark"})
    ctx.mods.remember("simple-voice-chat", "modrinth", {"project_id": "simple-voice-chat"})
    monkeypatch.setitem(
        notes.LOOK_ALIKES, ("modrinth", "simple-voice-chat", "paper"), LOOK["look_alikes"]
    )
    await change.change_type(ctx, "paper", technical=True, user="owner")

    asked = []

    class FakeModrinth:
        async def latest_for(self, project, minecraft, *, like=None):
            asked.append((project, minecraft))
            return {"version_id": "v"} if project == "spark" else None

    monkeypatch.setattr(ctx.mods, "site", lambda name: FakeModrinth())
    found = await change.similar(ctx)
    assert [(s["kind"], s["project"]) for s in found["suggestions"]] == [
        ("same", "spark"),
        ("look_alike", "hangar:42"),
    ]
    # Only add-ons the app has a record of are asked about: no name search.
    assert sorted(asked) == [("simple-voice-chat", MC), ("spark", MC)]
    assert every_jar(folder / "plugins") == [], "nothing was added for the owner"


# ---------------------------------------------------------------- Bedrock <-> Java (Chunker)
@pytest.mark.parametrize(
    ("edition", "version", "wanted"),
    [
        ("java", "1.21.1", "JAVA_1_21_1"),
        ("java", "1.21", "JAVA_1_21"),
        ("java", "26.1", "JAVA_26_1"),
        ("bedrock", "1.21.50.07", "BEDROCK_1_21_50"),
    ],
)
def test_chunkers_format_comes_from_a_checked_version(edition, version, wanted):
    assert chunker.output_format(edition, version) == wanted


@pytest.mark.parametrize(
    ("edition", "version"),
    [
        ("java", "1.21.12"),
        ("java", "24w40a"),
        ("java", "1.7.10"),
        ("bedrock", "1.15.0"),
        ("java", "1.21; calc"),
    ],
)
def test_a_version_the_pinned_chunker_doesnt_list_is_refused(edition, version):
    with pytest.raises(InstallError, match=f"Chunker {chunker.VERSION} can't change"):
        chunker.output_format(edition, version)


@pytest.fixture
def fake_chunker(monkeypatch, tmp_path):
    """Chunker's jar and the PC's Java, faked: records how it was started and
    writes a Java world with a Nether, as Chunker would."""
    jar = tmp_path / "tools" / "chunker-cli.jar"
    jar.parent.mkdir()
    jar.write_bytes(b"PK fake chunker")
    monkeypatch.setattr(chunker, "JAR", jar)
    monkeypatch.setattr(chunker, "check_java", lambda java: None)
    monkeypatch.setattr(create_module, "java_problem", lambda *args, **kwargs: None)
    calls: list[dict] = []
    real = asyncio.create_subprocess_exec
    outcome = {"world": True, "code": 0}

    async def fake(*args, **kwargs):
        if str(jar) not in args:
            return await real(*args, **kwargs)
        calls.append({"argv": list(args), **kwargs})
        argv = list(args)
        source, output = Path(argv[argv.index("-i") + 1]), Path(argv[argv.index("-o") + 1])
        calls[-1]["read"] = sorted(p.name for p in source.iterdir())
        if outcome["world"]:
            (output / "DIM-1").mkdir(parents=True)
            (output / "level.dat").write_bytes(b"converted")
            (output / "DIM-1" / "r.0.0.mca").write_bytes(b"nether")

        class Done:
            returncode = outcome["code"]

            async def communicate(self):
                text = b"Converting from Bedrock 1.21.50 to Java 1.21.1\n"
                return text + (
                    b"" if outcome["world"] else b"Failed to find suitable reader\n"
                ), b""

        return Done()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    return calls, outcome


def bedrock_server(ctx) -> Path:
    ctx.config.set("server.type", "bedrock")
    ctx.server.mc_version = "1.21.50.07"
    world = ctx.config.server_dir / "worlds" / "Bedrock level"
    (world / "db").mkdir(parents=True)
    (world / "level.dat").write_bytes(b"bedrock level")
    (world / "db" / "000005.ldb").write_bytes(b"chunks")
    return world


async def test_a_bedrock_world_becomes_a_new_java_server(
    core, ctx, network, fake_chunker, tmp_path
):
    calls, _ = fake_chunker
    world = bedrock_server(ctx)
    before = sorted((p.relative_to(world), p.read_bytes()) for p in world.rglob("*") if p.is_file())
    found = chunker.suggestion(core, ctx)
    assert found["name"] == "Survival (Java)" and "villagers" in found["warning"]

    with pytest.raises(InstallError, match="Tick the box"):
        await chunker.convert(
            core,
            ctx,
            type_id="paper",
            minecraft=MC,
            name="x",
            directory=str(tmp_path / "x"),
            eula_accepted=True,
            understood=False,
        )
    assert calls == []

    result = await chunker.convert(
        core,
        ctx,
        type_id="paper",
        minecraft=MC,
        name=found["name"],
        directory=str(tmp_path / "java copy"),
        eula_accepted=True,
        understood=True,
        user="owner",
    )
    (call,) = calls
    argv = call["argv"]
    assert argv[1].startswith("-Xmx") and argv[2:4] == ["-jar", str(chunker.JAR)]
    assert argv[-2:] == ["-f", "JAVA_1_21_1"] and "shell" not in call
    assert call["stdin"] == asyncio.subprocess.DEVNULL
    source = Path(argv[argv.index("-i") + 1])
    assert call["read"] == ["db", "level.dat"] and source.name == "Bedrock level"
    assert source.is_relative_to(core.config.data_dir), "Chunker read a copy, in the app's folder"

    new = Path(result["directory"])
    assert (new / "world" / "level.dat").read_bytes() == b"converted"
    assert (new / "world_nether" / "DIM-1" / "r.0.0.mca").read_bytes() == b"nether"
    assert core.get_server(result["server_id"]).config.server.type == "paper"
    # The original is untouched, and the copy Chunker read is gone.
    after = sorted((p.relative_to(world), p.read_bytes()) for p in world.rglob("*") if p.is_file())
    assert after == before and ctx.config.server.type == "bedrock"
    assert not list((core.config.data_dir / chunker.WORK).iterdir())


async def test_a_failed_chunker_run_shows_its_log_and_leaves_nothing(
    core,
    ctx,
    network,
    fake_chunker,
    tmp_path,
):
    _, outcome = fake_chunker
    outcome["world"] = False  # Chunker printed an error and still exited with 0
    bedrock_server(ctx)
    servers = set(core.servers)
    with pytest.raises(Exception, match="full log is at") as caught:
        await chunker.convert(
            core,
            ctx,
            type_id="vanilla",
            minecraft=MC,
            name="Copy",
            directory=str(tmp_path / "copy"),
            eula_accepted=True,
            understood=True,
        )
    assert "Failed to find suitable reader" in str(caught.value)
    assert set(core.servers) == servers and not (tmp_path / "copy").exists()
    logs = list((ctx.config.data_dir / install_module.INSTALL_LOG).iterdir())
    assert logs and "-jar" in logs[0].read_text(encoding="utf-8")


async def test_a_bedrock_version_chunker_doesnt_know_is_refused(
    core, ctx, network, fake_chunker, tmp_path
):
    calls, _ = fake_chunker
    bedrock_server(ctx)
    ctx.server.mc_version = "1.15.0.1"
    with pytest.raises(InstallError, match="can't change Bedrock 1.15.0.1"):
        await chunker.convert(
            core,
            ctx,
            type_id="vanilla",
            minecraft=MC,
            name="Copy",
            directory=str(tmp_path / "copy"),
            eula_accepted=True,
            understood=True,
        )
    assert calls == []


async def test_missing_java_is_said_plainly(core, ctx, network, monkeypatch, tmp_path):
    from agent.minecraft import java as java_module

    monkeypatch.setattr(
        java_module,
        "detect_java",
        lambda java: java_module.JavaInfo(requested=java, error="not found"),
    )
    with pytest.raises(InstallError, match="needs Java 17 or newer, and this PC has no Java"):
        chunker.check_java("java")
