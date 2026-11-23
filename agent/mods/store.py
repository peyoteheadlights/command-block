"""One copy of each mod and plugin file, shared by every server (the store).

Each file is kept once in the agent's data folder, under
``files/<first 2 of sha256>/<sha256>``. The name is the file's hash, so two
different files can never clash. Every server that uses it gets a hard link
to that one copy: two names for one file on disk, like two Java variables
pointing at one object. The file is only really gone when the last name is
removed.

Where a link can't be made (the server is on another drive, a FAT32/exFAT
drive, a network folder) the server gets a normal copy instead, and the
Mods page says so. Symlinks are never used: they need Administrator or
Developer Mode on Windows, and the app skips them anyway.

Rules that keep sharing safe:

* Nothing writes a stored file in place. A new version is always a new
  file, linked in with a rename. Changing a shared file would change it on
  every server at once.
* With the separate Windows user on (ADR 0007), each stored file says
  "no writing or deleting" for that user. A hard link has one set of rights
  for all its names, so a bad mod on one server can't change a jar another
  server loads.
* ``tidy()`` removes a stored file only when nothing links to it any more
  and no saved version (``mod_versions``) needs it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("msc.mods.store")

FOLDER = "files"
CHUNK = 1024 * 1024
# The daily Share identical files and tidy: a while after the agent
# starts, then once a day, with a short pause between files so a weak mini
# PC doesn't slow down.
FIRST_RUN_DELAY = 30 * 60
RUN_EVERY = 24 * 3600
PAUSE = 0.2


def hashes_of(path: Path) -> dict[str, Any]:
    """sha256, sha1 and sha512 of a file, read once. Modrinth and
    CurseForge look files up by sha1 or sha512, so they're kept too."""
    found = {name: hashlib.new(name) for name in ("sha256", "sha1", "sha512")}
    size = 0
    with open(path, "rb") as file:
        while chunk := file.read(CHUNK):
            size += len(chunk)
            for digest in found.values():
                digest.update(chunk)
    return {**{name: d.hexdigest() for name, d in found.items()}, "size": size}


class Store:
    def __init__(self, db, root: Path, lock: Callable[[Path], bool] | None = None):
        self.db = db
        self.root = Path(root)
        # Takes writing away from the separate Windows user; True when done.
        self.lock = lock

    def path_of(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256

    # ------------------------------------------------------------------ place
    def place(self, source: Path, target: Path) -> bool:
        """Put ``source``'s content at ``target`` as a link to the one stored
        copy (adding it to the store first when it's new). ``source`` may be
        ``target`` itself. Returns True when linked, False when ``target``
        had to be a normal copy. ``target`` is swapped in with one rename,
        never written in place."""
        hashes = hashes_of(source)
        stored = self._keep(source, hashes, Path(target).name)
        return self._link(stored, Path(target))

    def _keep(self, source: Path, hashes: dict[str, Any], name: str) -> Path:
        stored = self.path_of(hashes["sha256"])
        row = self.db.query_one("SELECT * FROM stored_files WHERE sha256 = ?", (hashes["sha256"],))
        if row and stored.is_file() and stored.stat().st_size == hashes["size"]:
            return stored
        stored.parent.mkdir(parents=True, exist_ok=True)
        temp = stored.with_name(stored.name + ".part")
        shutil.copyfile(source, temp)
        os.replace(temp, stored)
        locked = bool(self.lock and self.lock(stored))
        self.db.execute(
            "INSERT OR REPLACE INTO stored_files "
            "(sha256, sha1, sha512, size, name, first_seen, locked) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                hashes["sha256"],
                hashes["sha1"],
                hashes["sha512"],
                hashes["size"],
                name,
                row["first_seen"] if row else time.time(),
                int(locked),
            ),
        )
        return stored

    @staticmethod
    def _link(stored: Path, target: Path) -> bool:
        if target.exists() and os.path.samefile(stored, target):
            return True
        temp = target.with_name(f".{target.name}.link")
        temp.unlink(missing_ok=True)
        try:
            os.link(stored, temp)
            linked = True
        except OSError:
            shutil.copyfile(stored, temp)
            linked = False
        try:
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return linked

    # ------------------------------------------------------------------ find
    def find(
        self, sha256: str | None = None, sha1: str | None = None, sha512: str | None = None
    ) -> Path | None:
        """The stored file with one of these hashes, or None. Checked by its
        sha256 before it's handed out, so a changed file is never used."""
        for column, value in (("sha512", sha512), ("sha256", sha256), ("sha1", sha1)):
            if not value:
                continue
            row = self.db.query_one(
                f"SELECT sha256 FROM stored_files WHERE {column} = ?", (value.lower(),)
            )
            if not row:
                continue
            stored = self.path_of(row["sha256"])
            if stored.is_file() and hashes_of(stored)["sha256"] == row["sha256"]:
                return stored
            return None
        return None

    def read(self, **hashes: str | None) -> bytes | None:
        found = self.find(**hashes)
        return found.read_bytes() if found else None

    # ------------------------------------------------------------------ upkeep
    def lock_missing(self) -> int:
        """Take writing away on files stored before the separate Windows
        user was turned on. Normally there are none."""
        if not self.lock:
            return 0
        done = 0
        for row in self.db.query("SELECT sha256 FROM stored_files WHERE locked = 0"):
            stored = self.path_of(row["sha256"])
            if stored.is_file() and self.lock(stored):
                self.db.execute(
                    "UPDATE stored_files SET locked = 1 WHERE sha256 = ?", (row["sha256"],)
                )
                done += 1
        return done

    def tidy(self) -> int:
        """Remove stored files nothing links to and no saved version needs.
        Never call it while a job runs: a job may be about to link one."""
        needed = {
            r["sha256"]
            for r in self.db.query(
                "SELECT DISTINCT sha256 FROM mod_versions WHERE sha256 IS NOT NULL"
            )
        }
        removed = 0
        for row in self.db.query("SELECT sha256 FROM stored_files"):
            sha256 = row["sha256"]
            stored = self.path_of(sha256)
            if stored.is_file() and (stored.stat().st_nlink > 1 or sha256 in needed):
                continue
            try:
                stored.unlink(missing_ok=True)
            except OSError as exc:
                log.info("couldn't remove stored file %s: %s", stored, exc)
                continue
            self.db.execute("DELETE FROM stored_files WHERE sha256 = ?", (sha256,))
            removed += 1
        return removed

    def usage(self) -> dict[str, int]:
        """Real disk use of the stored files, and how much more they would
        take if each name were its own copy. Read from the files on disk."""
        on_disk = saved = files = 0
        for row in self.db.query("SELECT sha256 FROM stored_files"):
            stored = self.path_of(row["sha256"])
            try:
                info = stored.stat()
            except OSError:
                continue
            files += 1
            on_disk += info.st_size
            # One name is the store's own; each name past the first in a
            # server or saved-versions folder would be a full copy.
            saved += info.st_size * max(0, info.st_nlink - 2)
        return {"files": files, "on_disk": on_disk, "saved": saved}


def for_config(db, config) -> Store:
    """The agent's store, in its data folder. Files get "no writing" for
    the separate Windows user while that setting is on."""
    from .. import serveruser

    def lock(path: Path) -> bool:
        return serveruser.is_on(config) and serveruser.deny_writes(path)

    return Store(db, folder(config), lock)


def folder(config) -> Path:
    """The store's folder: in the agent's own data folder, shared by every
    server (a server's config points its data_dir at its own folder)."""
    from ..config import ServerConfig

    shared = config.root if isinstance(config, ServerConfig) else config
    return Path(shared.data_dir) / FOLDER


# ---------------------------------------------------------------------- upkeep
async def share(ctx: Any) -> int:
    """Share identical files: link a stopped server's jars (and its saved
    versions) to the store. Uses the sha256 the scan already has. A server
    that's running, or busy with a job, is left alone (Windows locks a
    running server's jars). Returns how many files were linked."""
    mods = ctx.mods
    if ctx.server.running or ctx.core.jobs.risky_job(ctx.server_id):
        return 0
    if not mods.server_type.has_content:
        return 0
    found = [(Path(m.path), m.sha256) for m in await asyncio.to_thread(mods.scan)]
    saved = mods.db.query(
        "SELECT archive_path, sha256 FROM mod_versions WHERE server_id = ?",
        (ctx.server_id,),
    )
    found += [(Path(r["archive_path"]), r["sha256"]) for r in saved]
    linked = 0
    for path, sha256 in found:
        if ctx.server.running:  # started meanwhile: stop here
            break
        try:
            if await asyncio.to_thread(_share_one, mods, path, sha256):
                linked += 1
        except OSError as exc:
            log.info("couldn't share %s: %s", path, exc)
        await asyncio.sleep(PAUSE)
    return linked


def _share_one(mods: Any, path: Path, sha256: str | None) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    stored = mods.store.path_of(sha256) if sha256 else None
    if stored is not None and stored.is_file() and os.path.samefile(stored, path):
        return False
    if path.parent == mods.mods_dir:
        mods.place_jar(path, path)  # also notes whether this server's drive can link
    else:
        mods.store.place(path, path)
    return True


async def upkeep(core: Any) -> None:
    """Share identical files on each stopped server, one at a time, then
    tidy the store, but only when no job is running."""
    for ctx in list(core.servers.values()):
        try:
            await share(ctx)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("sharing %s's files failed", ctx.name, exc_info=True)
    if core.jobs.recent(running_only=True):
        return
    await asyncio.to_thread(for_config(core.db, core.config).tidy)


class Upkeep:
    def __init__(self, core: Any):
        self.core = core
        self._task: asyncio.Task | None = None

    async def _loop(self) -> None:
        await asyncio.sleep(FIRST_RUN_DELAY)
        while True:
            try:
                await upkeep(self.core)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("the daily mod file upkeep failed", exc_info=True)
            await asyncio.sleep(RUN_EVERY)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="mod-file-upkeep")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
