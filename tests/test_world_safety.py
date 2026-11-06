"""World data stays safe when a restore, Undo or backup goes wrong."""

import shutil
import zipfile

import pytest

from agent import changes, safechange
from agent.backups.manager import BackupError, BackupManager
from agent.database.db import Database
from agent.events import EventBus
from agent.minecraft.process import MinecraftServer


@pytest.fixture
def backups(config):
    bus = EventBus()
    db = Database(config.database_path)
    db.register_server("test", "Test", str(config.server_dir))
    server = MinecraftServer(config, bus, db)
    return BackupManager(config, bus, db, server)


def _fail_half_way(monkeypatch, config):
    """Extraction writes one file of the new world, then the disk is full."""

    def extractall(self, path, members=None):
        (config.server_dir / "world").mkdir(exist_ok=True)
        (config.server_dir / "world" / "half.dat").write_text("half written")
        (config.server_dir / "world_nether").mkdir(exist_ok=True)
        (config.server_dir / "world_nether" / "half.dat").write_text("half written")
        raise OSError("No space left on device")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", extractall)


def _no_put_back(monkeypatch):
    """Moving files aside works; moving them back is refused."""
    real_move = shutil.move

    def move(src, dst):
        if ".replaced-" in str(src):
            raise OSError("Access is denied")
        return real_move(src, dst)

    monkeypatch.setattr(shutil, "move", move)


async def test_a_restore_that_fails_half_way_puts_the_old_world_back(backups, config, monkeypatch):
    marker = config.server_dir / "world" / "marker.dat"
    marker.write_text("from the backup")
    nether = config.server_dir / "world_nether"
    nether.mkdir(exist_ok=True)
    (nether / "region.mca").write_text("nether")
    created = await backups.create(user="tester", includes=["world", "world_nether"])
    marker.write_text("the world now")
    shutil.rmtree(nether)

    _fail_half_way(monkeypatch, config)
    with pytest.raises(BackupError, match="previous files were put back"):
        await backups.restore(created["id"], user="tester")

    assert marker.read_text() == "the world now"
    assert not (config.server_dir / "world" / "half.dat").exists()
    # A folder the server didn't have before is not left half written.
    assert not nether.exists()
    assert not list(config.server_dir.glob("world.replaced-*"))


async def test_a_failed_put_back_does_not_say_it_put_things_back(backups, config, monkeypatch):
    created = await backups.create(user="tester")
    _fail_half_way(monkeypatch, config)
    _no_put_back(monkeypatch)
    with pytest.raises(BackupError) as caught:
        await backups.restore(created["id"], user="tester")

    assert "put back" not in str(caught.value).replace("could not be put back", "")
    assert "world.replaced-" in str(caught.value)
    assert list(config.server_dir.glob("world.replaced-*"))


def _power_cut_half_way(monkeypatch, config):
    """Extraction writes one file, then the agent is gone: nothing after
    this line runs, not even the put-back."""

    def extractall(self, path, members=None):
        (config.server_dir / "world").mkdir(exist_ok=True)
        (config.server_dir / "world" / "half.dat").write_text("half written")
        raise SystemExit("power cut")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", extractall)


def _restarted(config):
    """The agent after a restart: the same database, nothing in memory."""
    bus = EventBus()
    db = Database(config.database_path)
    return MinecraftServer(config, bus, db), bus


async def test_after_a_power_cut_mid_restore_the_old_world_is_put_back(
    backups, config, monkeypatch
):
    marker = config.server_dir / "world" / "marker.dat"
    marker.write_text("from the backup")
    created = await backups.create(user="tester")
    marker.write_text("the world now")
    _power_cut_half_way(monkeypatch, config)
    with pytest.raises(SystemExit):
        await backups.restore(created["id"], user="tester")
    monkeypatch.undo()

    server, bus = _restarted(config)
    assert "didn't finish" in " ".join(server.preflight().problems)
    seen = []
    bus.subscribe(seen.append)
    await safechange.recover(server)

    assert marker.read_text() == "the world now"
    assert not (config.server_dir / "world" / "half.dat").exists()
    assert safechange.unfinished(server.db) is None
    assert [e.type for e in seen] == ["change_interrupted"]
    assert "put back" in seen[0].message


async def test_a_power_cut_put_back_that_fails_keeps_the_server_from_starting(
    backups, config, monkeypatch
):
    created = await backups.create(user="tester")
    _power_cut_half_way(monkeypatch, config)
    with pytest.raises(SystemExit):
        await backups.restore(created["id"], user="tester")
    monkeypatch.undo()
    _no_put_back(monkeypatch)

    server, _ = _restarted(config)
    await safechange.recover(server)
    problems = " ".join(server.preflight().problems)
    assert "didn't finish" in problems and "safety-" in problems
    monkeypatch.undo()

    # Restoring a backup is how the owner fixes it, and then it can start.
    restarted = BackupManager(config, EventBus(), server.db, server)
    await restarted.restore(created["id"], user="tester")
    assert safechange.unfinished(server.db) is None
    assert "didn't finish" not in " ".join(server.preflight().problems)


async def test_a_failed_put_back_at_runtime_keeps_the_server_from_starting(
    backups, config, monkeypatch
):
    created = await backups.create(user="tester")
    _fail_half_way(monkeypatch, config)
    _no_put_back(monkeypatch)
    with pytest.raises(BackupError):
        await backups.restore(created["id"], user="tester")
    assert "didn't finish" in " ".join(backups.server.preflight().problems)


async def test_other_changes_wait_while_one_is_left_unfinished(backups, config, monkeypatch):
    created = await backups.create(user="tester")
    backups.db.set_setting(
        safechange.UNFINISHED, {"title": "Restoring x", "backup": "safety-1.zip", "moved": []}
    )

    async def change(job):
        return {}

    plan = safechange.SafeChange(title="Changing game settings", change=change)
    with pytest.raises(safechange.SafeChangeError, match="safety-1.zip"):
        await safechange.run_safe_change(backups.server, backups, plan)
    await backups.restore(created["id"], user="tester")
    assert safechange.unfinished(backups.db) is None


async def test_a_finished_change_leaves_no_record(backups, config):
    created = await backups.create(user="tester")
    await backups.restore(created["id"], user="tester")
    assert safechange.unfinished(backups.db) is None


async def _a_change(backups, config, text="changed"):
    """One change on the undo stack: marker.dat goes from 'before' to text."""
    marker = config.server_dir / "world" / "marker.dat"
    marker.write_text("before")

    async def change(job):
        marker.write_text(text)
        return {}

    plan = safechange.SafeChange(title="Changing the marker", change=change)
    await safechange.run_safe_change(backups.server, backups, plan)
    return marker


async def test_an_undo_cut_off_half_way_can_be_pressed_again(backups, config, monkeypatch):
    marker = await _a_change(backups, config)
    _power_cut_half_way(monkeypatch, config)
    with pytest.raises(SystemExit):
        await backups.changes.undo("tester")
    monkeypatch.undo()
    # A real power cut doesn't get to put the row back on the undo stack.
    backups.db.execute("UPDATE changes SET undone = ?", (changes.UNDOING,))

    server, _ = _restarted(config)
    restarted = BackupManager(config, EventBus(), server.db, server)
    interrupted = await safechange.recover(server)
    restarted.changes.settle(interrupted)

    assert marker.read_text() == "changed"
    assert [c["title"] for c in restarted.changes.undo_stack()] == ["Changing the marker"]
    await restarted.changes.undo("tester")
    assert marker.read_text() == "before"


async def test_an_undo_that_finished_is_never_replayed(backups, config, monkeypatch):
    marker = await _a_change(backups, config)

    def cut(*args):
        raise SystemExit("power cut")

    monkeypatch.setattr(backups.changes, "_moved", cut)
    with pytest.raises(SystemExit):
        await backups.changes.undo("tester")
    monkeypatch.undo()
    assert marker.read_text() == "before"

    server, _ = _restarted(config)
    restarted = BackupManager(config, EventBus(), server.db, server)
    restarted.changes.settle(await safechange.recover(server))

    # The world is already undone: the same Undo is not offered again.
    assert restarted.changes.undo_stack() == []
    assert marker.read_text() == "before"


async def test_while_an_undo_runs_it_is_on_neither_stack(backups, config, monkeypatch):
    await _a_change(backups, config)
    seen = []
    real = backups.restore

    async def restore(*args, **kwargs):
        seen.append((backups.changes.undo_stack(), backups.changes.redo_stack()))
        return await real(*args, **kwargs)

    monkeypatch.setattr(backups, "restore", restore)
    await backups.changes.undo("tester")
    assert seen == [([], [])]
    assert [c["title"] for c in backups.changes.redo_stack()] == ["Changing the marker"]


class _DeafServer:
    """A running server whose console refuses every command."""

    def __init__(self, fails: int):
        self.running = True
        self.bus = EventBus()
        self.fails = fails
        self.sent: list[str] = []

    async def send_command(self, command, internal=False):
        self.sent.append(command)
        if len(self.sent) <= self.fails:
            raise RuntimeError("the console didn't take it")


async def test_saving_left_off_is_tried_again_then_alerted(monkeypatch):
    from agent.backups import hold

    monkeypatch.setattr(hold, "RETRY_SECONDS", 0)
    server = _DeafServer(fails=2)
    seen = []
    server.bus.subscribe(seen.append)
    await hold.resume_saving(server, "save-on", "the backup")
    assert server.sent == ["save-on", "save-on"]
    assert [e.type for e in seen] == ["saving_off"]
    assert "the backup" in seen[0].message


async def test_saving_turned_back_on_by_the_retry_sends_no_alert(monkeypatch):
    from agent.backups import hold

    monkeypatch.setattr(hold, "RETRY_SECONDS", 0)
    server = _DeafServer(fails=1)
    seen = []
    server.bus.subscribe(seen.append)
    await hold.resume_saving(server, "save resume", "the backup")
    assert server.sent == ["save resume", "save resume"]
    assert seen == []


async def test_a_cancelled_backup_leaves_no_half_written_archive(backups, config, monkeypatch):
    import asyncio

    real = backups._zip_sync

    def zip_then_cancel(target, *args, **kwargs):
        real(target, *args, **kwargs)
        raise asyncio.CancelledError

    monkeypatch.setattr(backups, "_zip_sync", zip_then_cancel)
    with pytest.raises(asyncio.CancelledError):
        await backups.create(user="tester")
    assert list(config.backup_dir.iterdir()) == []


async def test_a_backup_cut_off_by_a_power_cut_is_cleared_by_the_next_one(backups, config):
    left = config.backup_dir / "manual-20261106-120000.zip.part"
    left.write_bytes(b"half a zip")
    result = await backups.create(user="tester")
    assert not left.exists()
    assert [p.name for p in config.backup_dir.iterdir()] == [result["name"]]


# ------------------------------------------------------------ one busy answer
@pytest.fixture
async def core(tmp_path):
    from agent.core import AgentCore

    from .conftest import build_multi_config

    core = AgentCore(build_multi_config(tmp_path))
    yield core
    await core.jobs.stop()
    core.db.close()


async def _while_a_job_runs(core, ctx, check):
    import asyncio

    gate = asyncio.Event()

    async def work(job):
        await gate.wait()

    task = asyncio.create_task(
        core.jobs.run("mods", "Installing Sodium", work, server_id=ctx.server_id, risky=True)
    )
    await asyncio.sleep(0.05)
    try:
        return await check()
    finally:
        gate.set()
        await task


async def test_start_waits_for_a_risky_job_and_names_it(core):
    from agent.minecraft.process import ServerError

    ctx = core.get_server("survival")

    async def check():
        with pytest.raises(ServerError, match="Installing Sodium"):
            await ctx.server.start(actor="tester")

    await _while_a_job_runs(core, ctx, check)
    assert not ctx.server.running


async def test_start_waits_for_a_backup_that_runs_without_a_job(core):
    ctx = core.get_server("survival")
    ctx.backups._running = True
    try:
        assert "Backing up" in " ".join(ctx.server.preflight().problems)
    finally:
        ctx.backups._running = False


async def test_a_server_held_by_a_change_is_not_removed(core):
    from agent.minecraft.process import ServerError

    ctx = core.get_server("survival")
    ctx.server.held_by = "Restoring x"
    try:
        with pytest.raises(ServerError, match="Restoring x"):
            await core.remove_server("survival")
    finally:
        ctx.server.held_by = None


async def test_autosleep_waits_while_a_backup_runs(core):
    ctx = core.get_server("survival")
    ctx.backups._running = True
    try:
        assert ctx.busy() is not None
    finally:
        ctx.backups._running = False
    assert ctx.busy() is None


async def test_restart_is_refused_before_the_stop_while_a_job_runs(core):
    from agent.minecraft.process import ServerError

    ctx = core.get_server("survival")
    await ctx.server.start(actor="tester")
    try:

        async def check():
            with pytest.raises(ServerError, match="Installing Sodium"):
                await ctx.server.restart(actor="tester")
            assert ctx.server.running

        await _while_a_job_runs(core, ctx, check)
    finally:
        await ctx.server.stop()
