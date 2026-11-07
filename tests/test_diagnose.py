"""Diagnose server: the test player's steps, its download, and a run against
the fake server (with a stand-in for the Node.js bot)."""

from __future__ import annotations

import asyncio
import io
import json
import sys
import tarfile

import pytest

from agent import diagnose, joinbot
from agent.core import AgentCore
from agent.security.paths import PathSafetyError
from agent.security.permissions import SERVER_DIAGNOSE, SERVER_PERMISSIONS

from .conftest import build_bedrock_config, build_multi_config

ALL_OK = [{"step": s, "ok": True} for s in joinbot.STEPS]


def stand_in(monkeypatch, tmp_path, lines: list[dict]) -> None:
    """join.js replaced by a Python script that prints ``lines``. What it
    was given on stdin is kept in job.json (one per run, in jobs.json)."""
    (tmp_path / "lines.json").write_text(json.dumps(lines), encoding="utf-8")
    script = tmp_path / "bot.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "here = pathlib.Path(__file__).parent\n"
        "job = sys.stdin.readline()\n"
        "(here / 'job.json').write_text(job)\n"
        "with open(here / 'jobs.json', 'a') as jobs:\n"
        "    jobs.write(job)\n"
        "for line in json.loads((here / 'lines.json').read_text()):\n"
        "    print(json.dumps(line), flush=True)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(joinbot, "ensure", lambda: (sys.executable, script))


async def test_a_join_that_gets_through_every_step(monkeypatch, tmp_path):
    stand_in(monkeypatch, tmp_path, ALL_OK)
    seen: list = []
    steps = await joinbot.join("127.0.0.1", 25565, "CommandBlock", None, "hi", seen.append)
    assert steps == ALL_OK and seen == ALL_OK


async def test_a_kick_says_what_the_server_said(monkeypatch, tmp_path):
    kicked = [
        {"step": "connect", "ok": True},
        {"step": "login", "ok": False, "kick": "You are not white-listed on this server!"},
    ]
    stand_in(monkeypatch, tmp_path, kicked)
    assert await joinbot.join("127.0.0.1", 1, "CommandBlock", None, "hi") == kicked


async def test_a_bot_that_stops_between_steps_is_never_a_pass(monkeypatch, tmp_path):
    stand_in(monkeypatch, tmp_path, ALL_OK[:3])  # connect, login, spawn ok, then nothing
    steps = await joinbot.join("127.0.0.1", 1, "CommandBlock", None, "hi")
    assert steps[-1] == {
        "step": "chunks",
        "ok": False,
        "error": "the test player stopped without saying why",
    }
    stand_in(monkeypatch, tmp_path, [])
    steps = await joinbot.join("127.0.0.1", 1, "CommandBlock", None, "hi")
    assert steps == [
        {"step": "connect", "ok": False, "error": "the test player stopped without saying why"}
    ]


def test_every_package_is_pinned_with_a_checksum():
    found = joinbot.packages()
    names = [path for path, _url, _sha in found]
    assert "node_modules/mineflayer" in names
    assert all(url.startswith("https://registry.npmjs.org/") for _p, url, _s in found)
    assert all(len(sha) == 128 for _p, _u, sha in found)  # sha512 in hex


def tarball(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


DATA_JS = b"""module.exports =
{
  'pc': {
    '1.21.4': {
      get blocks () { return require("./minecraft-data/data/pc/1.21.4/blocks.json") },
    }
  },
  'bedrock': {
    '1.21.0': {
      get blocks () { return require("./minecraft-data/data/bedrock/1.21.0/blocks.json") },
      proto: __dirname + '/minecraft-data/data/bedrock/1.21.0/proto.yml'
    },
    '1.26.60': {
      get blocks () { return require("./minecraft-data/data/bedrock/1.26.60/blocks.json") },
      get steve () { return require("./minecraft-data/data/bedrock/1.21.0/steve.json") },
      proto: __dirname + '/minecraft-data/data/bedrock/latest/proto.yml'
    }
  }
}
"""


def test_only_the_newest_bedrock_versions_data_is_unpacked(monkeypatch, tmp_path):
    monkeypatch.setattr(joinbot, "BEDROCK_VERSIONS", 1)
    target = tmp_path / "node_modules" / "minecraft-data"
    data = "package/minecraft-data/data/"
    joinbot._unpack_package(
        tarball(
            {
                "package/data.js": DATA_JS,
                f"{data}bedrock/1.21.0/blocks.json": b"old",
                f"{data}bedrock/1.21.0/proto.yml": b"old",
                f"{data}bedrock/1.21.0/steve.json": b"still read by 1.26.60",
                f"{data}bedrock/1.26.60/blocks.json": b"new",
                f"{data}bedrock/1.26.60/other.json": b"read by no version",
                f"{data}bedrock/latest/proto.yml": b"new",
                f"{data}bedrock/common/features.json": b"[]",
                f"{data}pc/1.21.4/blocks.json": b"{}",
            }
        ),
        target,
    )
    kept = {
        str(path.relative_to(target / "minecraft-data/data")).replace("\\", "/")
        for path in (target / "minecraft-data/data").rglob("*")
        if path.is_file()
    }
    assert kept == {
        "bedrock/1.21.0/steve.json",
        "bedrock/1.26.60/blocks.json",
        "bedrock/latest/proto.yml",
        "bedrock/common/features.json",
        "pc/1.21.4/blocks.json",
    }


def test_only_packages_the_bot_loads_are_downloaded():
    needed = joinbot.needed()
    for name in ("mineflayer", "bedrock-protocol", "jsp-raknet", "prismarine-auth"):
        assert f"node_modules/{name}" in needed
    for name in joinbot.LEFT_OUT:
        assert not any(path.endswith(f"node_modules/{name}") for path in needed)
    downloaded = [path for path, _url, _sha in joinbot.packages()]
    assert "node_modules/prismarine-xbox-services" not in downloaded  # kept in bot/vendor
    assert (joinbot.BOT_SOURCE / "vendor/prismarine-xbox-services/src/index.js").is_file()


def test_a_vendored_package_must_match_the_lock_file(monkeypatch):
    monkeypatch.setattr(joinbot, "VENDORED", {"node_modules/prismarine-xbox-services": "0" * 40})
    with pytest.raises(joinbot.BotError, match="another commit"):
        joinbot.packages()


def test_a_package_that_climbs_out_is_refused(tmp_path):
    with pytest.raises(PathSafetyError):
        joinbot._unpack_package(tarball({"package/../../evil.js": b"x"}), tmp_path / "pkg")
    assert not (tmp_path / "evil.js").exists()


def test_diagnose_is_owner_only_for_now():
    assert SERVER_DIAGNOSE not in SERVER_PERMISSIONS


@pytest.fixture
async def core(tmp_path):
    agent = AgentCore(build_multi_config(tmp_path))
    for ctx in agent.servers.values():
        await ctx.start()
    try:
        yield agent
    finally:
        for ctx in list(agent.servers.values()):
            if ctx.server.running:
                await ctx.server.stop()
            await ctx.stop()
        await agent.jobs.stop()
        agent.db.close()


def offline_mode(ctx, whitelist: bool = False) -> None:
    path = ctx.config.server_dir / "server.properties"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text + f"online-mode=false\nwhite-list={str(whitelist).lower()}\n", encoding="utf-8"
    )


async def test_a_stopped_server_is_started_and_joined(core, monkeypatch, tmp_path):
    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    stand_in(monkeypatch, tmp_path, ALL_OK)
    ctx = core.get_server("survival")
    offline_mode(ctx)
    _job, result = await core.jobs.run(
        "diagnose", "Diagnose", lambda job: diagnose.run(diagnose.Run(ctx, "admin"), job)
    )
    assert result["running"] is False and ctx.diagnosis is result
    assert result["lines"][0] == {
        "group": "The server",
        "what": "Start the server",
        "ok": True,
        "detail": "It was stopped, so Diagnose started it.",
    }
    joined = [line for line in result["lines"] if line["group"].startswith("Join from this PC")]
    assert [line["what"] for line in joined] == [diagnose.STEP_WORDS[s] for s in joinbot.STEPS]
    assert all(line["ok"] is True for line in joined)
    assert ctx.players.ignored == set()


async def test_a_failed_step_marks_the_rest_not_tried(core, monkeypatch, tmp_path):
    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    stand_in(monkeypatch, tmp_path, [{"step": "connect", "ok": False, "error": "ECONNREFUSED"}])
    ctx = core.get_server("survival")
    offline_mode(ctx)
    _job, result = await core.jobs.run(
        "diagnose", "Diagnose", lambda job: diagnose.run(diagnose.Run(ctx, "admin"), job)
    )
    joined = [line for line in result["lines"] if line["group"].startswith("Join from this PC")]
    assert joined[0] == {
        "group": joined[0]["group"],
        "what": "Reach the game port",
        "ok": False,
        "detail": "Nothing answers on that port.",
    }
    assert all(line["ok"] is None for line in joined[1:])


async def test_the_test_player_is_whitelisted_only_while_it_joins(core, monkeypatch, tmp_path):
    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    stand_in(monkeypatch, tmp_path, ALL_OK)
    ctx = core.get_server("survival")
    offline_mode(ctx, whitelist=True)
    sent: list[str] = []
    real = ctx.player_actions.send

    async def send(action, name, reason, **kw):
        sent.append(f"{action} {name}")
        return await real(action, name, reason, **kw)

    monkeypatch.setattr(ctx.player_actions, "send", send)
    await core.jobs.run(
        "diagnose", "Diagnose", lambda job: diagnose.run(diagnose.Run(ctx, "admin"), job)
    )
    assert sent == ["whitelist_add CommandBlock", "whitelist_remove CommandBlock"]


async def test_online_mode_without_a_sign_in_starts_nothing(core, monkeypatch, tmp_path):
    stand_in(monkeypatch, tmp_path, ALL_OK)
    ctx = core.get_server("survival")
    _job, result = await core.jobs.run(
        "diagnose", "Diagnose", lambda job: diagnose.run(diagnose.Run(ctx, "admin"), job)
    )
    assert [line["ok"] for line in result["lines"]] == [None]
    assert result["lines"][0]["detail"] == diagnose.SIGN_IN_FIRST
    assert result["sign_in"] is True
    assert not ctx.server.running


def signed_in(core, java: str | None = "Steve", gamertag: str | None = "Steve Gamer") -> None:
    from agent.security.records import write_secrets

    write_secrets(
        core.microsoft.path,
        {
            "cache": json.dumps({"live": {"token": "t"}}),
            "java": java or "",
            "gamertag": gamertag or "",
        },
    )


async def test_online_mode_joins_with_the_owners_java_account(core, monkeypatch, tmp_path):
    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    renewed = {"live": {"token": "new"}}
    stand_in(monkeypatch, tmp_path, [{"cache": renewed}, *ALL_OK])
    signed_in(core)
    ctx = core.get_server("survival")
    ignored: list = []

    class Seen(set):
        def add(self, name):
            ignored.append(name)
            super().add(name)

    monkeypatch.setattr(ctx.players, "ignored", Seen())
    _job, result = await core.jobs.run(
        "diagnose", "Diagnose", lambda job: diagnose.run(diagnose.Run(ctx, "admin"), job)
    )
    joined = [line for line in result["lines"] if line["group"].endswith("Java account")]
    assert [line["ok"] for line in joined] == [True] * len(joinbot.STEPS)
    job = json.loads((tmp_path / "job.json").read_text())
    assert job["auth"] == "microsoft" and job["edition"] == "java"
    assert job["cache"] == {"live": {"token": "t"}}
    assert core.microsoft.account() == renewed  # the renewed sign-in is kept
    assert ignored == ["Steve"]


async def test_an_account_without_java_says_so(core, monkeypatch, tmp_path):
    stand_in(monkeypatch, tmp_path, ALL_OK)
    signed_in(core, java=None)
    ctx = core.get_server("survival")
    _job, result = await core.jobs.run(
        "diagnose", "Diagnose", lambda job: diagnose.run(diagnose.Run(ctx, "admin"), job)
    )
    assert [line["ok"] for line in result["lines"]] == [None]
    assert "no Minecraft: Java Edition" in result["lines"][0]["detail"]
    assert result["sign_in"] is False and not ctx.server.running


async def test_a_run_out_sign_in_says_sign_in_again(core, monkeypatch, tmp_path):
    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    stand_in(
        monkeypatch,
        tmp_path,
        [{"step": "connect", "ok": True}, {"step": "login", "ok": False, "error": "signed out"}],
    )
    signed_in(core)
    ctx = core.get_server("survival")
    _job, result = await core.jobs.run(
        "diagnose", "Diagnose", lambda job: diagnose.run(diagnose.Run(ctx, "admin"), job)
    )
    login = next(line for line in result["lines"] if line["what"] == "Log in")
    assert login["ok"] is False and "Sign in with Microsoft again" in login["detail"]


def test_crossplay_adds_a_bedrock_join_through_geyser(tmp_path):
    config = build_multi_config(tmp_path, servers=[("survival", "Survival", 25565)])
    config.for_server("survival").set("server.crossplay", True)
    config.for_server("survival").set("server.bedrock_port", 19132)
    agent = AgentCore(config)
    try:
        ctx = agent.get_server("survival")
        assert [(j.edition, j.port, j.microsoft) for j in diagnose.joins(ctx)] == [
            ("java", 25565, True),
            ("bedrock", 19132, True),
        ]
        signed_in(agent)
        geyser = diagnose.joins(ctx)[1]
        assert diagnose.player_name(ctx, geyser) == ".Steve_Gamer"  # how Floodgate shows it
    finally:
        agent.db.close()


def test_a_bedrock_server_joins_as_a_bedrock_player(tmp_path):
    agent = AgentCore(build_bedrock_config(tmp_path))
    try:
        ctx = agent.get_server("bedrock")
        assert [(j.edition, j.microsoft) for j in diagnose.joins(ctx)] == [("bedrock", True)]
        path = ctx.config.server_dir / "server.properties"
        path.write_text(path.read_text(encoding="utf-8") + "online-mode=false\n", encoding="utf-8")
        assert [(j.edition, j.microsoft) for j in diagnose.joins(ctx)] == [("bedrock", False)]
        signed_in(agent)
        assert (
            diagnose.player_name(ctx, diagnose.Join("x", "bedrock", 19132, True)) == "Steve Gamer"
        )
    finally:
        agent.db.close()


def test_a_bedrock_version_the_bot_doesnt_know_is_not_checked():
    words = diagnose._why({"step": "connect", "ok": False, "error": "bedrock version 1.20.0"})
    assert words is not None and words.startswith("Not checked") and "1.20.0" in words


async def test_the_owner_is_asked_before_a_join_kicks_them(core, monkeypatch):
    signed_in(core)
    ctx = core.get_server("survival")
    assert diagnose.owner_playing(ctx) is None
    monkeypatch.setattr(ctx.players, "online", lambda: [{"username": "steve"}])
    assert diagnose.owner_playing(ctx) == "Steve"
    offline_mode(ctx)  # then the test player is CommandBlock: nobody is kicked
    assert diagnose.owner_playing(ctx) is None


async def test_sign_in_shows_the_code_then_keeps_the_sign_in(core, monkeypatch, tmp_path):
    cache = {"live": {"token": "secret"}}
    stand_in(
        monkeypatch,
        tmp_path,
        [
            {"code": "ABCD1234", "url": "https://www.microsoft.com/link", "expires_in": 900},
            {"cache": cache},
            {"signed_in": True, "java": "Steve", "gamertag": "Steve Gamer"},
        ],
    )
    status = await core.microsoft.sign_in()
    assert status["code"] == "ABCD1234" or status["signed_in"]
    for _ in range(100):
        if core.microsoft.status()["signed_in"]:
            break
        await asyncio.sleep(0.05)
    status = core.microsoft.status()
    assert status == {
        "signed_in": True,
        "java_name": "Steve",
        "gamertag": "Steve Gamer",
        "code": None,
        "url": None,
        "error": None,
    }
    assert json.loads((tmp_path / "job.json").read_text()) == {"do": "signin"}
    assert core.microsoft.account() == cache
    assert b"secret" not in json.dumps(status).encode()
    core.microsoft.sign_out()
    assert core.microsoft.account() is None and not core.microsoft.path.exists()


async def test_a_refused_sign_in_says_why(core, monkeypatch, tmp_path):
    stand_in(monkeypatch, tmp_path, [{"signed_in": False, "error": "fetch failed"}])
    status = await core.microsoft.sign_in()
    assert status["signed_in"] is False and "Couldn't reach Microsoft" in status["error"]


def test_microsoft_sign_in_is_owner_only():
    from agent.security.permissions import MICROSOFT_ACCOUNT

    assert MICROSOFT_ACCOUNT not in SERVER_PERMISSIONS


async def test_the_test_players_join_and_leave_are_skipped(core):
    ctx = core.get_server("survival")
    seen: list = []
    core.bus.subscribe(lambda event: seen.append(event))
    ctx.players.ignored.add("CommandBlock")
    await ctx.players.reconcile(["CommandBlock"])
    assert ctx.players.online() == []
    assert not [e for e in seen if e.type == "player_joined"]


def test_bot_script_is_shipped():
    assert (joinbot.BOT_SOURCE / "join.js").is_file()
    spec = (joinbot.PROJECT_ROOT / "packaging" / "app.spec").read_text()
    assert '"build" / "tools" / "diagnose"), "tools/diagnose"' in spec


def test_the_desktop_app_shows_the_list_as_it_comes(qapp, agent_http, monkeypatch, tmp_path):
    pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")
    from desktop.diagnose import DiagnoseWindow

    from .test_desktop_app import open_server, open_window, shown_text, sign_in, wait_for

    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    stand_in(monkeypatch, tmp_path, ALL_OK)
    offline_mode(agent_http.agent.app.state.core.servers["survival"])
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    wait_for(qapp, lambda: "Survival" in shown_text(window))
    open_server(qapp, window, "Survival")
    header = window.server_screen.header
    assert header.diagnose.isVisible()
    header.diagnose.click()
    dialog = next(w for w in qapp.topLevelWidgets() if isinstance(w, DiagnoseWindow))
    wait_for(qapp, lambda: "Done." in dialog.status.text(), timeout=60)
    words = shown_text(dialog)
    assert "✓  Start the server" in words and "✓  Leave" in words
    assert dialog.again.isVisible()
    dialog.close()
    window.close()


def test_only_node_exe_is_taken_from_the_nodejs_zip(tmp_path):
    import zipfile

    archive = tmp_path / "node.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(joinbot.NODE_EXE, b"MZ node")
        zf.writestr(joinbot.NODE_LICENSE, b"MIT")
        zf.writestr(joinbot.NODE_EXE.replace("node.exe", "npm.cmd"), b"npm")
    joinbot._unpack_node(archive, tmp_path / "node.exe")
    assert (tmp_path / "node.exe").read_bytes() == b"MZ node"
    assert (tmp_path / "LICENSE").read_bytes() == b"MIT"
    assert not (tmp_path / "npm.cmd").exists()


def test_the_installer_build_downloads_and_checks_everything(monkeypatch, tmp_path):
    import hashlib
    import zipfile

    import httpx

    from agent import downloads

    node = io.BytesIO()
    with zipfile.ZipFile(node, "w") as zf:
        zf.writestr(joinbot.NODE_EXE, b"MZ node")
        zf.writestr(joinbot.NODE_LICENSE, b"MIT")
    package = tarball({"package/index.js": b"module.exports = 1"})
    files = {"/dist/node.zip": node.getvalue(), "/pkg/-/pkg-1.0.0.tgz": package}

    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=files[request.url.path])

    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(serve))
    monkeypatch.setattr(joinbot, "NODE_ZIP", "https://nodejs.org/dist/node.zip")
    monkeypatch.setattr(joinbot, "NODE_ZIP_SHA256", hashlib.sha256(node.getvalue()).hexdigest())
    sha = hashlib.sha512(package).hexdigest()
    url = "https://registry.npmjs.org/pkg/-/pkg-1.0.0.tgz"
    monkeypatch.setattr(joinbot, "packages", lambda: [("node_modules/pkg", url, sha)])
    folder = tmp_path / "diagnose"
    asyncio.run(joinbot.prepare(folder))
    assert (folder / "node.exe").read_bytes() == b"MZ node"
    assert (folder / "node_modules/pkg/index.js").is_file()
    assert (folder / "join.js").is_file() and joinbot.ready(folder)
    assert not (folder / "node.zip").exists()

    # A package that doesn't match its pinned checksum: nothing is kept.
    monkeypatch.setattr(joinbot, "packages", lambda: [("node_modules/pkg", url, "0" * 128)])
    (folder / "ready.json").unlink()
    with pytest.raises(joinbot.BotError):
        asyncio.run(joinbot.prepare(folder))
    assert not folder.exists()


def test_the_desktop_app_signs_in_with_microsoft(qapp, agent_http, monkeypatch, tmp_path):
    pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")
    from desktop.diagnose import DiagnoseWindow

    from .test_desktop_app import open_window, shown_text, sign_in, wait_for

    stand_in(
        monkeypatch,
        tmp_path,
        [
            {"code": "ABCD1234", "url": "https://www.microsoft.com/link"},
            {"cache": {"live": {"token": "t"}}},
            {"signed_in": True, "java": "Steve", "gamertag": None},
        ],
    )
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    wait_for(qapp, lambda: "Survival" in shown_text(window))
    dialog = DiagnoseWindow(window, "survival", "Survival")
    dialog.show()
    # Online mode and nobody signed in: nothing is joined, the button is there.
    wait_for(qapp, lambda: "Done." in dialog.status.text(), timeout=30)
    assert dialog.sign_in.isVisible()
    monkeypatch.setattr(
        dialog, "start", lambda go_ahead=False: None
    )  # the join itself is tested above
    dialog.sign_in.click()
    wait_for(qapp, lambda: "Steve" in dialog.account.text(), timeout=30)
    assert dialog.sign_out.isVisible() and not dialog.sign_in.isVisible()
    dialog.sign_out.click()
    wait_for(qapp, lambda: dialog.sign_in.isVisible(), timeout=30)
    dialog.close()
    window.close()


# ------------------------------------------------- the changes (part 3)
async def diagnose_round(core, ctx, before=None, pick="all"):
    """One run, or (with ``before``) the round after picking its changes."""
    from agent import diagnosefix

    fixes = diagnosefix.picked(ctx, before, pick) if before else None
    out = diagnose.Run(ctx, "admin", before=before)
    _job, result = await core.jobs.run(
        "diagnose", "Diagnose", lambda job: diagnose.run(out, job, fixes)
    )
    return result


async def test_a_server_stopped_by_the_eula_gets_it_offered_then_joins(core, monkeypatch, tmp_path):
    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    monkeypatch.setenv("FAKE_CHECK_EULA", "1")
    stand_in(monkeypatch, tmp_path, ALL_OK)
    ctx = core.get_server("survival")
    offline_mode(ctx)
    first = await asyncio.wait_for(diagnose_round(core, ctx), 60)  # not START_WAIT
    assert first["lines"][0]["ok"] is False
    assert [(c["key"], c["safe"]) for c in first["changes"]] == [("eula", False)]

    second = await diagnose_round(core, ctx, first)
    assert second["round"] == 2
    assert "eula=true" in (ctx.config.server_dir / "eula.txt").read_text()
    assert all(line["ok"] is True for line in second["lines"])
    assert second["done"] == [
        {
            "title": "Accept the Minecraft EULA",
            "safe": False,
            "outcome": "kept",
            "detail": "Kept: that problem is gone.",
        }
    ]
    assert second["changes"] == []  # nothing new: the run ends


async def test_more_memory_that_didnt_help_is_put_back(core, monkeypatch, tmp_path):
    from agent import memory

    monkeypatch.setenv("FAKE_CRASH_ON_START", "1")  # runs out of memory whatever it gets
    monkeypatch.setattr(memory, "total_ram_mb", lambda: 16384)
    stand_in(monkeypatch, tmp_path, ALL_OK)
    ctx = core.get_server("survival")
    offline_mode(ctx)
    ctx.config.set("server.jvm_args", ["-Xms1G", "-Xmx2G"])
    first = await diagnose_round(core, ctx)
    assert [c["title"] for c in first["changes"]] == ["Give the server more memory (2 GB to 3 GB)"]
    assert first["changes"][0]["safe"] is True

    second = await diagnose_round(core, ctx, first, pick="safe")
    assert second["done"][0]["outcome"] == "put_back"
    assert list(ctx.config.server.jvm_args) == ["-Xms1G", "-Xmx2G"]
    assert second["changes"] == []  # tried once, never offered again


async def test_no_more_memory_when_the_pc_has_no_room(core, monkeypatch, tmp_path):
    from agent import memory

    monkeypatch.setenv("FAKE_CRASH_ON_START", "1")
    monkeypatch.setattr(memory, "total_ram_mb", lambda: 2048)
    stand_in(monkeypatch, tmp_path, ALL_OK)
    ctx = core.get_server("survival")
    offline_mode(ctx)
    ctx.config.set("server.jvm_args", ["-Xmx2G"])
    assert (await diagnose_round(core, ctx))["changes"] == []


NOT_LISTED = [
    {"step": "connect", "ok": True},
    {"step": "login", "ok": False, "kick": "You are not white-listed on this server!"},
]


async def test_the_owner_turned_away_by_the_whitelist_is_added(core, monkeypatch, tmp_path):
    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    stand_in(monkeypatch, tmp_path, NOT_LISTED)
    signed_in(core)
    ctx = core.get_server("survival")
    (ctx.config.server_dir / "whitelist.json").write_text("[]", encoding="utf-8")
    sent: list[str] = []

    async def send(action, name, reason="", **kw):
        sent.append(f"{action} {name}")

    monkeypatch.setattr(ctx.player_actions, "send", send)
    first = await diagnose_round(core, ctx)
    assert first["changes"] == [
        {
            "key": "whitelist Steve",
            "title": "Add Steve to the whitelist",
            "why": "The server turned your account (Steve) away because it isn't on the whitelist.",
            "safe": False,
        }
    ]
    stand_in(monkeypatch, tmp_path, ALL_OK)
    second = await diagnose_round(core, ctx, first)
    assert sent == ["whitelist_add Steve"]
    assert second["done"][0]["outcome"] == "kept"
    assert ctx.backups.changes.undo_stack()[0]["title"] == "Diagnose changes"


async def test_apply_safe_leaves_risky_changes_and_one_that_didnt_help_goes_back(
    core, monkeypatch, tmp_path
):
    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    stand_in(monkeypatch, tmp_path, NOT_LISTED)
    signed_in(core)
    ctx = core.get_server("survival")
    sent: list[str] = []

    async def send(action, name, reason="", **kw):
        sent.append(f"{action} {name}")

    monkeypatch.setattr(ctx.player_actions, "send", send)
    first = await diagnose_round(core, ctx)
    from agent import diagnosefix

    assert diagnosefix.picked(ctx, first, "safe") == []  # whitelisting is risky
    second = await diagnose_round(core, ctx, first)  # still turned away
    assert sent == ["whitelist_add Steve", "whitelist_remove Steve"]
    assert second["done"][0]["outcome"] == "put_back"


async def test_a_server_that_doesnt_answer_gets_a_restart(core, monkeypatch, tmp_path):
    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    stand_in(monkeypatch, tmp_path, [{"step": "connect", "ok": False, "error": "ETIMEDOUT"}])
    ctx = core.get_server("survival")
    offline_mode(ctx)
    first = await diagnose_round(core, ctx)
    assert [(c["key"], c["safe"]) for c in first["changes"]] == [("restart", True)]
    pid = ctx.server.pid
    second = await diagnose_round(core, ctx, first, pick="safe")
    assert ctx.server.pid != pid
    assert second["done"][0]["outcome"] == "no_help"
    assert second["changes"] == []


def test_deny_all_makes_nothing(multi_client):
    core = multi_client.app.state.core
    ctx = core.get_server("survival")
    path = "/api/servers/survival/server/diagnose/changes"
    assert multi_client.post(path, json={"pick": "all"}).status_code == 409  # nothing waiting
    risky = {"key": "eula", "title": "Accept the Minecraft EULA", "why": "", "safe": False}
    run = diagnose.Run(ctx, "admin")
    run.finish()
    run.result["changes"] = [risky]
    answer = multi_client.post(path, json={"pick": "none"})
    assert answer.status_code == 200 and answer.json()["job_id"] is None
    shown = multi_client.get("/api/servers/survival/server/diagnose").json()
    assert shown["changes"] == []
    assert shown["done"][0]["outcome"] == "denied"
    assert not (ctx.config.server_dir / "eula.txt").exists()


def test_the_desktop_app_offers_the_changes_and_applies_them(
    qapp, agent_http, monkeypatch, tmp_path
):
    pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")
    from desktop.diagnose import DiagnoseWindow

    from .test_desktop_app import open_server, open_window, shown_text, sign_in, wait_for

    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    monkeypatch.setenv("FAKE_CHECK_EULA", "1")
    stand_in(monkeypatch, tmp_path, ALL_OK)
    offline_mode(agent_http.agent.app.state.core.servers["survival"])
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    wait_for(qapp, lambda: "Survival" in shown_text(window))
    open_server(qapp, window, "Survival")
    window.server_screen.header.diagnose.click()
    # An earlier test's window can still be in the list, closed.
    dialog = next(
        w for w in qapp.topLevelWidgets() if isinstance(w, DiagnoseWindow) and w.isVisible()
    )
    wait_for(qapp, lambda: dialog.picks["all"].isVisible(), timeout=60)
    words = shown_text(dialog)
    assert "Risky changes" in words and "Accept the Minecraft EULA" in words
    assert not dialog.picks["safe"].isEnabled()  # nothing safe to apply
    assert not dialog.again.isVisible()
    dialog.picks["all"].click()
    wait_for(qapp, lambda: "Kept: that problem is gone." in shown_text(dialog), timeout=60)
    wait_for(qapp, lambda: dialog.again.isVisible(), timeout=60)
    assert "✓  Leave" in shown_text(dialog)
    assert not dialog.picks["all"].isVisible()
    dialog.close()
    window.close()
