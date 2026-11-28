"""Undo and Redo: two stacks per server.

Every change made through the safe-change routine (agent/safechange.py) that
its backup can reverse goes on the server's undo stack. Undo takes the newest
one, puts back the backup made just before it and moves it to the redo stack;
Redo does the opposite. Undo and Redo are not changes themselves, so pressing
Undo again goes one step further back. Any new change empties the redo stack.

Both stacks live in one table. Rows with ``undone = 0`` are the undo stack,
its top the highest id; rows with ``undone = 1`` are the redo stack, its top
the lowest id (the change undone last). Every undone row is newer than every
row still done, so the two never interleave.

While Undo or Redo runs, its row is marked as moving (``undone`` -1 or 2),
which puts it on neither stack. If the agent stops in the middle, ``settle``
at the next start puts it back where it was when the files were put back,
or drops it when the move had finished, so the same backup is never put
back twice.

An entry can only be reversed while its backup exists. Backups made before a
change are kept 14 days (BackupManager.apply_retention), so that is how far
back Undo reaches: an entry whose backup has gone drops off its stack, with
everything beneath it, which could only be reached through it.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .backups.manager import BackupManager


# ``undone`` while Undo or Redo of the row runs.
UNDOING = -1
REDOING = 2


class ChangeError(RuntimeError):
    """Written for people: why Undo or Redo can't run."""


class ChangeHistory:
    def __init__(self, db, server_id: str, backups: BackupManager):
        self.db = db
        self.server_id = server_id
        self.backups = backups

    # ------------------------------------------------------------- the stacks
    def push(
        self,
        title: str,
        backup_id: int,
        scope: list[str] | None,
        user: str | None,
        job_id: str | None = None,
    ) -> int:
        """A change has been made: it goes on top of the undo stack, and
        what was undone before it can no longer be redone."""
        self.clear_redo()
        cur = self.db.execute(
            "INSERT INTO changes (server_id, job_id, title, user, scope, before_backup_id, "
            "undone, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (
                self.server_id,
                job_id,
                title,
                user,
                json.dumps(sorted(scope)) if scope is not None else None,
                backup_id,
                time.time(),
            ),
        )
        return int(cur.lastrowid)

    def clear_redo(self) -> None:
        self.db.execute("DELETE FROM changes WHERE server_id = ? AND undone = 1", (self.server_id,))

    def undo_stack(self) -> list[dict[str, Any]]:
        """Newest first."""
        return self._stack(undone=False)

    def redo_stack(self) -> list[dict[str, Any]]:
        """The change undone last first."""
        return self._stack(undone=True)

    def _stack(self, undone: bool) -> list[dict[str, Any]]:
        rows = self.db.query(
            f"SELECT * FROM changes WHERE server_id = ? AND undone = ? "
            f"ORDER BY id {'ASC' if undone else 'DESC'}",
            (self.server_id, 1 if undone else 0),
        )
        backups = {b["id"]: b for b in self.backups.list_backups()}
        entries: list[dict[str, Any]] = []
        for row in rows:
            entry = self._entry(row, backups, undone)
            if entry is None:
                # Gone, so nothing beneath it can be reached either.
                self._drop([r["id"] for r in rows[len(entries) :]])
                break
            entries.append(entry)
        return entries

    def _entry(
        self, row: dict[str, Any], backups: dict[int, dict[str, Any]], undone: bool
    ) -> dict[str, Any] | None:
        backup = backups.get(row["after_backup_id"] if undone else row["before_backup_id"])
        if not backup or not backup.get("exists") or backup.get("status") != "ok":
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "user": row["user"],
            "job_id": row["job_id"],
            "made_at": row["created_at"],
            "undone_at": row["moved_at"] if undone else None,
            "scope": json.loads(row["scope"]) if row["scope"] else None,
            # What Undo (or Redo) would put back, and from when.
            "backup_id": backup["id"],
            "backup": backup["name"],
            "backup_at": backup["created_at"],
        }

    def _drop(self, ids: list[int]) -> None:
        for change_id in ids:
            self.db.execute("DELETE FROM changes WHERE id = ?", (change_id,))

    # ------------------------------------------------------------- undo, redo
    @staticmethod
    def _expected(top: dict[str, Any], change_id: int | None) -> dict[str, Any]:
        """The app names the change it asked about; if someone else moved one
        since, nothing is put back."""
        if change_id is not None and top["id"] != change_id:
            raise ChangeError("This server's changes moved on since. Look again, then try again.")
        return top

    async def undo(
        self, user: str, start_after: bool = False, change_id: int | None = None
    ) -> dict[str, Any]:
        stack = self.undo_stack()
        if not stack:
            raise ChangeError("There is nothing to undo on this server.")
        top = self._expected(stack[0], change_id)
        result = await self._move(top, user, start_after, "undoing", UNDOING, 0)
        self._moved(
            "UPDATE changes SET undone = 1, after_backup_id = ?, moved_at = ?, moved_by = ? "
            "WHERE id = ?",
            (result["safety_backup"]["id"], time.time(), user, top["id"]),
        )
        return {**result, "change": top}

    async def redo(
        self, user: str, start_after: bool = False, change_id: int | None = None
    ) -> dict[str, Any]:
        stack = self.redo_stack()
        if not stack:
            raise ChangeError("There is nothing to redo on this server.")
        top = self._expected(stack[0], change_id)
        result = await self._move(top, user, start_after, "redoing", REDOING, 1)
        # The backup just made is from before the change again, so it is
        # what the next Undo of this change puts back.
        self._moved(
            "UPDATE changes SET undone = 0, before_backup_id = ?, moved_at = ?, moved_by = ? "
            "WHERE id = ?",
            (result["safety_backup"]["id"], time.time(), user, top["id"]),
        )
        return {**result, "change": top}

    async def _move(
        self, entry: dict[str, Any], user: str, start_after: bool, doing: str, mark: int, was: int
    ) -> dict[str, Any]:
        """Mark the row as moving first, then put the files back. A failure
        puts the row back on its stack."""
        self.db.execute("UPDATE changes SET undone = ? WHERE id = ?", (mark, entry["id"]))
        try:
            return await self._put_back(entry, user, start_after, doing)
        except BaseException:
            self.db.execute("UPDATE changes SET undone = ? WHERE id = ?", (was, entry["id"]))
            raise

    def _moved(self, sql: str, params: tuple[Any, ...]) -> None:
        self.db.execute(sql, params)

    def settle(self, interrupted: bool) -> None:
        """At agent start, for a row left moving: when the change it was in
        was cut off (its files were put back), it goes back on its stack;
        when the files had already moved, it is dropped, as its new backup
        isn't known."""
        if interrupted:
            for mark, was in ((UNDOING, 0), (REDOING, 1)):
                self.db.execute(
                    "UPDATE changes SET undone = ? WHERE server_id = ? AND undone = ?",
                    (was, self.server_id, mark),
                )
        else:
            self.db.execute(
                "DELETE FROM changes WHERE server_id = ? AND undone IN (?, ?)",
                (self.server_id, UNDOING, REDOING),
            )

    async def _put_back(
        self, entry: dict[str, Any], user: str, start_after: bool, doing: str
    ) -> dict[str, Any]:
        scope = set(entry["scope"]) if entry["scope"] is not None else None
        # The restore takes its own backup first: that is the way back.
        return await self.backups.restore(
            entry["backup_id"],
            user=user,
            start_after=start_after,
            only=scope,
            history="none",
            note=f"Before {doing}: {entry['title'][:1].lower()}{entry['title'][1:]}",
        )
