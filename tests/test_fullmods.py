"""Diagnose server: the join with all mods (agent/fullmods.py), with
HeadlessMC and the game faked: a try "crashes" when a broken jar is in it."""

# ruff: noqa: F811
# (pytest fixtures from tests/test_diagnose.py: "core" is used by name)
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from agent import diagnose, diagnosefix, fullmods, headlessmc
from agent.minecraft.console import ConsoleBuffer
from agent.mods.jarinfo import ModDependency, ModInfo

from .test_diagnose import core, diagnose_round, no_other_ways_in, offline_mode  # noqa: F401
from .test_mods import make_jar


def mod(mod_id: str, *needs: str) -> ModInfo:
    return ModInfo(
        filename=f"{mod_id}.jar",
        path=f"{mod_id}.jar",
        mod_id=mod_id,
        dependencies=[ModDependency(n, "*") for n in needs],
    )


def test_a_mod_comes_with_the_mods_it_needs():
    every = [mod("a"), mod("b", "c"), mod("c"), mod("d")]
    names = [m.mod_id for m in fullmods.with_needs([every[1]], every)]
    assert names == ["b", "c"]


def crash_when(bad: set[str], tries: list):
    async def crashes(chosen):
        tries.append([m.mod_id for m in chosen])
        return any(m.mod_id in bad for m in chosen)

    return crashes


async def test_halving_finds_the_one_mod_and_checks_it():
    every = [mod(x) for x in "abcdefgh"]
    tries: list = []
    found = await fullmods.find_broken(every, crash_when({"f"}, tries))
    assert found is not None and found.mod_id == "f"
    assert len(tries) == 5  # 3 halvings for 8 mods, then it alone and all but it
    assert tries[-2] == ["f"]
    assert "f" not in tries[-1] and len(tries[-1]) == 7


async def test_two_mods_together_is_not_blamed_on_one():
    every = [mod(x) for x in "abcd"]

    async def crashes(chosen):  # only a and d together stop it
        ids = {m.mod_id for m in chosen}
        return {"a", "d"} <= ids

    assert await fullmods.find_broken(every, crashes) is None


async def test_running_out_of_time_finds_nothing():
    async def crashes(chosen):
        return None

    assert await fullmods.find_broken([mod("a"), mod("b")], crashes) is None


# ------------------------------------------------------------ one try
def watching(lines: list[str], exits: bool = False):
    console = ConsoleBuffer()
    ctx = SimpleNamespace(server=SimpleNamespace(console=console))
    process = SimpleNamespace(returncode=1 if exits else None)

    async def feed():
        await asyncio.sleep(0.05)
        for line in lines:
            console.append(line)

    return ctx, process, feed


@pytest.mark.parametrize(
    ("lines", "exits", "joined", "reached", "why"),
    [
        (["Steve joined the game"], False, True, True, "Joined and stayed"),
        (
            ["Steve (/127.0.0.1:5555) lost connection: Mod rejections: [create]"],
            False,
            False,
            True,
            "The server said: Mod rejections: [create]",
        ),
        ([], True, False, False, "The game closed before it reached the server."),
        ([], False, False, False, "didn't reach the server"),
    ],
)
async def test_the_servers_console_says_how_the_try_went(
    monkeypatch, lines, exits, joined, reached, why
):
    monkeypatch.setattr(fullmods, "STAY", 0.1)
    monkeypatch.setattr(fullmods, "POLL", 0.02)
    ctx, process, feed = watching(lines, exits)
    task = asyncio.create_task(feed())
    found = await fullmods._watch(ctx, process, "Steve", set(), 0, [], 0.5)
    await task
    assert (found.joined, found.reached) == (joined, reached)
    assert why in found.why


async def test_a_crash_says_the_last_error_the_game_printed():
    ctx, process, _feed = watching([], exits=True)
    output = ["Loading", "java.lang.RuntimeException: Mixin apply failed", "Bye"]
    found = await fullmods._watch(ctx, process, None, set(), 0, output, 1)
    assert "Mixin apply failed" in found.why


async def test_the_game_and_everything_it_started_is_stopped(tmp_path, monkeypatch):
    hmc = headlessmc.HeadlessMc(tmp_path)
    child = tmp_path / "child.py"
    child.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    script = tmp_path / "launcher.py"
    script.write_text(
        "import subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, {str(child)!r}])\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    async def ready():
        return hmc.exe

    monkeypatch.setattr(hmc, "ensure", ready)
    monkeypatch.setattr(hmc, "argv", lambda *command: [sys.executable, str(script)])
    async with hmc.started("launch") as process:
        pid = int(await process.stdout.readline())
    import psutil

    await asyncio.sleep(0.5)
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


# ------------------------------------------------------------ a whole run
@pytest.fixture
def modded(core, monkeypatch):
    """Survival as an offline Fabric server with three mods; ``broken`` jars
    stop the fake game, ``seen`` keeps each try's jar names."""
    ctx = core.servers["survival"]
    offline_mode(ctx)
    mods = ctx.config.server_dir / "mods"
    mods.mkdir(exist_ok=True)
    for mod_id in ("lib", "create", "sodium"):
        make_jar(mods / f"{mod_id}.jar", mod_id, depends={"lib": "*"} if mod_id != "lib" else {})
    state = {"broken": {"sodium.jar"}, "seen": [], "versions": [], "updated": []}

    async def attempt(ctx, jars, timeout):
        names = {j.name for j in jars}
        state["seen"].append(sorted(names))
        if names & state["broken"]:
            return fullmods.Try(False, False, "The game closed before it reached the server.")
        return fullmods.Try(True, True, "Joined and stayed for 10 seconds.")

    class Site:
        async def versions(self, project, minecraft):
            return state["versions"]

        async def download(self, version):
            return b"jar", version["file"]["filename"], "x"

    async def update(filename, user, version_id):
        state["updated"].append((filename, version_id))
        state["broken"].discard(filename)
        return {"rollback_archive": "old"}

    monkeypatch.setattr(fullmods, "attempt", attempt)
    monkeypatch.setattr(fullmods, "FREE_MB", 0)
    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    monkeypatch.setattr(ctx.mods, "origin", lambda m: ("modrinth", m.mod_id))
    monkeypatch.setattr(ctx.mods, "site", lambda name: Site())
    monkeypatch.setattr(ctx.mods, "update", update)
    monkeypatch.setattr(ctx.server, "mc_version", "1.21.1", raising=False)
    return ctx, state


def mod_lines(result):
    return [line for line in result["lines"] if line["group"] == fullmods.GROUP]


async def run_with_mods(core, ctx, before=None, pick="all"):
    out = diagnose.Run(ctx, "admin", before=before, mods=True)
    fixes = diagnosefix.picked(ctx, before, pick) if before else None
    _job, result = await core.jobs.run(
        "diagnose", "Diagnose", lambda job: diagnose.run(out, job, fixes)
    )
    return result


async def test_another_version_that_starts_is_offered_then_kept(core, modded, monkeypatch):
    ctx, state = modded
    from agent import joinbot

    async def bot(*args, on_step=None, **kwargs):
        for step in joinbot.STEPS:
            on_step({"step": step, "ok": True})

    monkeypatch.setattr(joinbot, "join", bot)
    state["versions"] = [
        {"version_id": "v3", "version_number": "3.0", "file": {"filename": "sodium-3.jar"}},
    ]
    result = await run_with_mods(core, ctx)
    lines = mod_lines(result)
    assert lines[0]["ok"] is False  # all three mods: the game closes
    assert "sodium" in lines[1]["detail"] and lines[1]["broken"] == "sodium.jar"
    assert lines[2]["ok"] is True and "3.0" in lines[2]["detail"]
    assert [c["title"] for c in result["changes"]] == ["Change Sodium to version 3.0"]
    assert result["changes"][0]["safe"]
    # Every try had lib along: create and sodium need it.
    assert all("lib.jar" in seen for seen in state["seen"])

    after = await run_with_mods(core, ctx, before=result)
    assert state["updated"] == [("sodium.jar", "v3")]
    assert mod_lines(after)[0]["ok"] is True
    assert after["done"][-1]["outcome"] == "kept"


async def test_no_version_starts_so_turning_it_off_is_risky(core, modded, monkeypatch):
    ctx, state = modded
    state["versions"] = [
        {"version_id": "v2", "version_number": "2.0", "file": {"filename": "sodium.jar"}},
    ]
    result = await run_with_mods(core, ctx)
    assert mod_lines(result)[2]["ok"] is False
    assert [(c["title"], c["safe"]) for c in result["changes"]] == [("Turn off Sodium", False)]
    safe = await run_with_mods(core, ctx, before=result, pick="safe")
    assert (ctx.config.server_dir / "mods" / "sodium.jar").is_file()  # Apply safe leaves it
    assert [c["title"] for c in safe["changes"]] == ["Turn off Sodium"]  # still waiting


async def test_turning_it_off_keeps_the_jar(core, modded):
    ctx, _state = modded
    result = await run_with_mods(core, ctx)
    after = await run_with_mods(core, ctx, before=result)
    folder = ctx.config.server_dir / "mods"
    assert (folder / "sodium.jar.disabled").is_file() and not (folder / "sodium.jar").exists()
    assert after["done"][-1]["outcome"] == "kept"


async def test_a_server_that_turns_the_game_away_is_not_halved(core, modded, monkeypatch):
    ctx, state = modded

    async def turned_away(ctx, jars, timeout):
        state["seen"].append(jars)
        return fullmods.Try(False, True, "The server said: Mod rejections")

    monkeypatch.setattr(fullmods, "attempt", turned_away)
    result = await run_with_mods(core, ctx)
    assert len(state["seen"]) == 1
    assert [x["detail"] for x in mod_lines(result)] == ["The server said: Mod rejections"]


async def test_not_checked_says_why(core, modded, monkeypatch):
    ctx, _state = modded
    monkeypatch.setattr(fullmods, "FREE_MB", 10**9)
    result = await run_with_mods(core, ctx)
    assert "free memory" in mod_lines(result)[0]["detail"]
    monkeypatch.setattr(fullmods, "FREE_MB", 0)
    (ctx.config.server_dir / "server.properties").write_text("online-mode=true\n")
    result = await run_with_mods(core, ctx)
    assert mod_lines(result)[0]["detail"] == fullmods.SIGN_IN_FIRST
    assert result["sign_in_mods"]


def test_a_mod_server_is_asked_about_the_join_with_all_mods(multi_client, monkeypatch):
    monkeypatch.setattr(fullmods, "wanted", lambda ctx: True)
    answer = multi_client.post("/api/servers/survival/server/diagnose", json={}).json()
    assert answer == {"job_id": None, "ask": None, "ask_mods": fullmods.ASK}


async def test_headlessmc_sign_in_shows_the_page_then_keeps_the_name(tmp_path, monkeypatch):
    hmc = headlessmc.HeadlessMc(tmp_path)
    hmc.folder.mkdir()
    script = tmp_path / "login.py"
    script.write_text(
        "import time\n"
        "print('To login visit https://www.microsoft.com/link?otc=ABCD1234', flush=True)\n"
        "time.sleep(0.3)\n"
        "print('Logged in to account Steve successfully!', flush=True)\n",
        encoding="utf-8",
    )

    async def ready():
        return hmc.exe

    monkeypatch.setattr(hmc, "ensure", ready)
    monkeypatch.setattr(hmc, "argv", lambda *command: [sys.executable, str(script)])
    status = await hmc.sign_in()
    assert status["code"] == "ABCD1234" and status["url"].startswith("https://www.microsoft.com/")
    await hmc._task
    assert hmc.status()["name"] == "Steve" and hmc.status()["signed_in"]
    hmc.sign_out()
    assert not hmc.status()["signed_in"]


def test_headlessmc_is_pinned_to_github_with_a_checksum():
    from agent.downloads import DownloadError, check_url

    assert len(headlessmc.EXE_SHA256) == 64
    with pytest.raises(DownloadError):
        check_url(headlessmc.EXE_URL)  # only while it downloads it
    from agent import downloads

    with downloads.allow_releases(headlessmc.REPO):
        assert check_url(headlessmc.EXE_URL)


def test_the_desktop_app_asks_about_the_join_with_all_mods(qapp, agent_http, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")
    from desktop.diagnose import DiagnoseWindow

    from .test_desktop_app import open_window, shown_text, sign_in, wait_for

    offline_mode(agent_http.agent.app.state.core.servers["survival"])
    asked: list = []
    monkeypatch.setattr(fullmods, "wanted", lambda ctx: True)

    async def nothing(out, job):
        out.line(fullmods.GROUP, "Join with all 1 mods", None, "Not checked: test")

    monkeypatch.setattr(fullmods, "check", nothing)
    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    monkeypatch.setattr(diagnose, "joins", lambda ctx: [])
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    wait_for(qapp, lambda: "Survival" in shown_text(window))
    monkeypatch.setattr(window, "confirm", lambda *a: asked.append(a[1]) or True)
    dialog = DiagnoseWindow(window, "survival", "Survival")
    dialog.show()
    wait_for(qapp, lambda: "Done." in dialog.status.text(), timeout=30)
    assert asked == [fullmods.ASK] and dialog.mods is True
    assert "Join with all mods" in shown_text(dialog)
    dialog.close()
    window.close()
