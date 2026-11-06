"""The safe-change routine: one way to make any risky change to a server.

    1. stop the server if the change needs it
    2. take a backup and verify it (a change never starts without one)
    3. make the change
    4. check the result
    5. if the change or the check fails, put the backup back
    6. start the server again if asked

The backup taken in step 2 is the change's undo: a change that succeeds goes
on the server's undo stack (agent/changes.py), and Undo puts that backup back.
Restores, game settings, world and modpack imports, version and type changes
and duplication all run through this routine.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .events import Event

if TYPE_CHECKING:
    from .backups.manager import BackupManager
    from .jobs import JobHandle
    from .minecraft.process import MinecraftServer

log = logging.getLogger("msc.safechange")

# A change in the middle of moving files is written down in the database
# (setting UNFINISHED: its title, the backup taken before it and every
# file moved aside), so a power cut can't leave a half-moved world that
# starts as if nothing happened. Cleared once the change is recorded.
UNFINISHED = "unfinished_change"

CheckResult = tuple[bool, str]


class SafeChangeError(RuntimeError):
    pass


@dataclass
class SafeChange:
    title: str  # what the change does, e.g. "Restore manual-20261005.zip"
    change: Callable[[JobHandle | None], Awaitable[dict[str, Any]]]
    # Returns (ok, detail). None means the change checks itself.
    check: Callable[[dict[str, Any]], Awaitable[CheckResult]] | None = None
    stop_server: bool = True
    start_after: bool = False
    take_backup: bool = True
    backup_name: str = "pre-change"
    backup_note: str | None = None
    backup_includes: list[str] | None = field(default=None)
    # False when the change already puts things back itself if it fails part
    # way (restoring a backup does), so the safety backup is not extracted
    # over files that are already back as they were.
    undo_on_change_error: bool = True
    # What a successful change does to Undo and Redo: "push" puts it on the
    # undo stack; "clear_redo" is a change Undo can't reverse (a version
    # change, which has its own Roll back), so it only empties Redo; "none"
    # is Undo and Redo themselves, or a change that leaves this server as it
    # was (duplicating it).
    history: str = "push"
    # What Undo puts back: these top-level names, or (None) what the backup
    # holds.
    undo_scope: list[str] | None = None
    # True for a restore: it may run while an earlier change is left
    # unfinished, because putting a backup back is how that is fixed.
    puts_back: bool = False


async def run_safe_change(
    server: MinecraftServer,
    backups: BackupManager,
    change: SafeChange,
    job: JobHandle | None = None,
    user: str = "system",
) -> dict[str, Any]:
    """Run ``change`` with a verified backup behind it. Returns what the
    change returned plus ``safety_backup``, ``undo`` and ``check``."""
    from .minecraft.process import ServerError

    def step(name: str) -> None:
        if job:
            job.step(name)

    was_running = server.running
    if change.stop_server and server.held_by:
        raise SafeChangeError(f"Wait for '{server.held_by}' to finish first")
    left = unfinished(server.db)
    if change.stop_server and left and not change.puts_back:
        raise SafeChangeError(blocked_message(left))
    if change.stop_server:
        # Held from before the stop until the result is checked, so nothing
        # (a click, a schedule, another device) starts Minecraft on files
        # that are half changed.
        server.held_by = change.title
        server.db.set_setting(UNFINISHED, {"title": change.title, "backup": None, "moved": []})
    try:
        result, detail, safety = await _change_held(server, backups, change, job, user, was_running)
    except BaseException:
        # Anything still moved aside means the files are half changed: the
        # record stays, and the server won't start until they are put back.
        if change.stop_server and not _still_aside(server.db):
            server.db.set_setting(UNFINISHED, None)
        raise
    finally:
        if change.stop_server:
            server.held_by = None

    change_id = _record(backups, change, safety, job, user)
    if change.stop_server:
        server.db.set_setting(UNFINISHED, None)

    started = False
    if change.start_after:
        step("Starting the server")
        try:
            await server.start(actor=user)
            started = await server.wait_online()
        except ServerError as exc:
            await server.bus.publish(
                Event(
                    type="restore_start_failed",
                    level="error",
                    message=f"The server did not start after the change: {exc}",
                )
            )
    return {
        **result,
        "safety_backup": safety,
        "undo": {"kind": "restore_backup", "backup_id": safety["id"], "backup": safety["name"]}
        if safety and change.history == "push"
        else None,
        "change_id": change_id,
        "check": detail,
        "was_running": was_running,
        "server_started": started,
    }


def _record(
    backups: BackupManager,
    change: SafeChange,
    safety: dict[str, Any] | None,
    job: JobHandle | None,
    user: str,
) -> int | None:
    """Put a finished change on the undo stack, or just empty Redo."""
    history = getattr(backups, "changes", None)
    if history is None or change.history == "none":
        return None
    if change.history != "push" or not safety:
        history.clear_redo()
        return None
    scope = change.undo_scope if change.undo_scope is not None else change.backup_includes
    return history.push(change.title, safety["id"], scope, user, job_id=job.id if job else None)


async def _change_held(
    server: MinecraftServer,
    backups: BackupManager,
    change: SafeChange,
    job: JobHandle | None,
    user: str,
    was_running: bool,
) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    """Steps 1 to 5: stop, back up, change, check, undo on failure."""
    from .minecraft.state import ExitReason

    def step(name: str) -> None:
        if job:
            job.step(name)

    if change.stop_server and was_running:
        step("Stopping the server")
        await server.bus.publish(
            Event(type="restore_stopping", message=f"Stopping the server for: {change.title}")
        )
        await server.stop(actor=user, reason=ExitReason.USER_STOP)

    safety: dict[str, Any] | None = None
    if change.take_backup:
        # create() raises if the backup fails or does not verify, and then
        # nothing below has happened.
        safety = await backups.create(
            name=change.backup_name,
            kind="safety",
            includes=change.backup_includes,
            user=user,
            note=change.backup_note or before(change.title),
            job=job,
        )
        if change.stop_server:
            record = unfinished(server.db) or {"title": change.title, "moved": []}
            server.db.set_setting(UNFINISHED, {**record, "backup": safety["name"]})

    step(change.title)
    try:
        result = await change.change(job)
    except Exception as exc:
        undone = await _undo(server, backups, safety, job) if change.undo_on_change_error else ""
        raise SafeChangeError(f"{exc}{undone}") from exc

    detail = ""
    if change.check:
        step("Checking the result")
        ok, detail = await change.check(result)
        if not ok:
            undone = await _undo(server, backups, safety, job)
            raise SafeChangeError(f"The result could not be verified: {detail}{undone}")
    return result, detail, safety


def before(title: str) -> str:
    """The note on a backup made before a change, which is also how the
    Backups list labels it: "Before changing game settings"."""
    return f"Before {title[:1].lower()}{title[1:]}"


async def _undo(
    server: MinecraftServer,
    backups: BackupManager,
    safety: dict[str, Any] | None,
    job: JobHandle | None,
) -> str:
    """Put the safety backup back after a failed change. Returns a sentence
    for the error message saying what happened."""
    if not safety:
        return ""
    if job:
        job.step("Putting the previous files back")
    try:
        await backups.put_back(safety["id"])
    except Exception as exc:  # pragma: no cover - reported, never hidden
        log.exception("could not put the safety backup back")
        return (
            f" Putting the previous files back also failed ({exc}); the backup "
            f"{safety['name']} is still there to restore."
        )
    # The files are as the backup has them again: nothing is half moved.
    record = unfinished(server.db)
    if record:
        server.db.set_setting(UNFINISHED, {**record, "moved": []})
    return f" The previous files were put back from the backup {safety['name']}."


# ----------------------------------------------------------------- unfinished
def unfinished(db) -> dict[str, Any] | None:
    """The change this server is in the middle of, or None."""
    return db.get_setting(UNFINISHED)


def note_moved(db, live: Path, retired: Path | None) -> None:
    """A change is about to move ``live`` aside to ``retired`` (None: write
    ``live`` where nothing was). Written before the file moves, so a power
    cut never loses track of it."""
    record = unfinished(db)
    if record is not None:
        record["moved"].append([str(live), str(retired) if retired else None])
        db.set_setting(UNFINISHED, record)


def _moved(record: dict[str, Any]) -> list[tuple[Path, Path | None]]:
    return [(Path(live), Path(retired) if retired else None) for live, retired in record["moved"]]


def _still_aside(db) -> bool:
    record = unfinished(db)
    return record is not None and any(r is not None and r.exists() for _, r in _moved(record))


def blocked_message(record: dict[str, Any]) -> str:
    backup = record.get("backup")
    fix = (
        f"Restore the backup {backup} to go back to how it was before."
        if backup
        else "Restore a backup to put the world back."
    )
    return f"'{record['title']}' didn't finish and its files are half changed. {fix}"


def put_back_moved(moved: list[tuple[Path, Path | None]]) -> list[str]:
    """Undo moving files aside: remove what was half written in their place,
    then move them back (``None``: nothing was there before). Newest first.
    Returns the names still aside."""
    left = []
    for live, retired in reversed(moved):
        # The half-written copy is only deleted once the old one is back, so
        # a failed put-back never leaves the server with neither.
        half = live.with_name(f"{live.name}.half-written")
        try:
            if live.exists() or live.is_symlink():
                live.rename(half)
            if retired is not None:
                try:
                    shutil.move(str(retired), str(live))
                except OSError:
                    if half.exists() or half.is_symlink():
                        half.rename(live)
                    raise
            if half.is_dir() and not half.is_symlink():
                shutil.rmtree(half)
            elif half.exists() or half.is_symlink():
                half.unlink()
        except OSError:
            log.exception("could not put %s back", live)
            left.append(retired.name if retired else live.name)
    return left


async def recover(server: MinecraftServer) -> bool:
    """At agent start: a change that was cut off (a power cut, the agent
    killed) puts the moved-aside files back, as a failed change would have.
    If that fails too, the server stays blocked until a restore. Returns
    whether a change was cut off."""
    record = unfinished(server.db)
    if record is None:
        return False
    moved = [(live, r) for live, r in _moved(record) if r is None or r.exists()]
    left = await asyncio.to_thread(put_back_moved, moved)
    if left:
        await server.bus.publish(
            Event(
                type="change_interrupted",
                level="error",
                message=(
                    f"The agent stopped in the middle of '{record['title']}', and the files "
                    f"from before could not be put back. {blocked_message(record)}"
                ),
                data={"title": record["title"], "backup": record.get("backup"), "left": left},
            )
        )
        return True
    server.db.set_setting(UNFINISHED, None)
    done = "The files from before it were put back." if moved else "Nothing had changed yet."
    await server.bus.publish(
        Event(
            type="change_interrupted",
            level="warn",
            message=f"The agent stopped in the middle of '{record['title']}'. {done}",
            data={"title": record["title"], "backup": record.get("backup"), "left": []},
        )
    )
    return True
