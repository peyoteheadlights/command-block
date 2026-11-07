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

from .conftest import build_multi_config

ALL_OK = [{"step": s, "ok": True} for s in joinbot.STEPS]


def stand_in(monkeypatch, tmp_path, lines: list[dict]) -> None:
    """join.js replaced by a Python script that prints ``lines``."""
    (tmp_path / "lines.json").write_text(json.dumps(lines), encoding="utf-8")
    script = tmp_path / "bot.py"
    script.write_text(
        "import json, pathlib\n"
        "for line in json.loads((pathlib.Path(__file__).parent / 'lines.json').read_text()):\n"
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


def test_a_package_unpacks_without_bedrock_data(tmp_path):
    target = tmp_path / "node_modules" / "minecraft-data"
    joinbot._unpack_package(
        tarball(
            {
                "package/index.js": b"x",
                "package/minecraft-data/data/bedrock/1.21.0/blocks.json": b"big",
                "package/minecraft-data/data/bedrock/common/features.json": b"[]",
                "package/minecraft-data/data/pc/1.21.4/blocks.json": b"{}",
            }
        ),
        target,
    )
    assert (target / "index.js").is_file()
    assert (target / "minecraft-data/data/pc/1.21.4/blocks.json").is_file()
    assert (target / "minecraft-data/data/bedrock/common/features.json").is_file()
    assert not (target / "minecraft-data/data/bedrock/1.21.0").exists()


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


async def test_online_mode_is_not_checked_yet_and_nothing_starts(core, monkeypatch, tmp_path):
    stand_in(monkeypatch, tmp_path, ALL_OK)
    ctx = core.get_server("survival")
    _job, result = await core.jobs.run(
        "diagnose", "Diagnose", lambda job: diagnose.run(diagnose.Run(ctx, "admin"), job)
    )
    assert [line["ok"] for line in result["lines"]] == [None]
    assert "Microsoft account" in result["lines"][0]["detail"]
    assert not ctx.server.running


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
