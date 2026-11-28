"""Clean-up: find what a server folder no longer needs, show it, and move
the picked items into a clean-up bin, so Undo puts them back.

What it finds (never the world, mods or settings):

  * old logs: logs/*.log, *.log.gz and *.txt older than a week (never latest.log)
  * old crash reports: crash-reports/ files and Java's hs_err_pid*.log older
    than a week (the Crashes page keeps its own copy of each report)
  * folders set aside by a restore or a world import (``*.replaced-*``)
  * unused dimension data: <world>/dimensions/<name>/ where no installed mod
    or datapack has that name. Only on Java servers, and only when every
    mod could be read; when that can't be told, nothing is offered.

The bin is in the server's part of the agent data folder. Each clean-up is
one batch there with a list of what it moved. A batch is emptied for good
after BIN_DAYS days, or when the owner presses Empty now; until then Undo
moves everything back to where it was.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .minecraft.properties import world_folders
from .security.paths import directory_size, safe_join

if TYPE_CHECKING:
    from .core import ServerContext

log = logging.getLogger("msc.cleanup")

OLD_DAYS = 7
BIN_DAYS = 7
LOG_SUFFIXES = (".log", ".gz", ".txt")
MANIFEST = "cleanup.json"


class CleanupError(RuntimeError):
    """Written for people: why the clean-up can't do this."""


def bin_dir(config: Any) -> Path:
    """The server's clean-up bin, in its part of the agent data folder."""
    return config.resolve_data("cleanup-bin")


def _item(base: Path, path: Path, kind: str) -> dict[str, Any]:
    return {
        "path": path.relative_to(base).as_posix(),
        "kind": kind,
        "size_bytes": directory_size(path),
        "modified": path.stat().st_mtime,
    }


def _old_files(folder: Path, cutoff: float, keep: tuple[str, ...] = ()) -> list[Path]:
    if not folder.is_dir():
        return []
    return [
        f
        for f in sorted(folder.iterdir())
        if f.is_file()
        and not f.is_symlink()
        and f.name not in keep
        and f.suffix.lower() in LOG_SUFFIXES
        and f.stat().st_mtime < cutoff
    ]


def _namespaces(ctx: ServerContext, world: Path) -> set[str] | None:
    """Every name a mod or datapack gives its things, or None when that
    can't be told (a mod jar that couldn't be read)."""
    names = {"minecraft"}
    if ctx.config.server_type.content == "mods":
        try:
            mods = ctx.mods.scan()
        except Exception:  # no mods folder yet, or it can't be read
            return None
        index, known = ctx.mods.provided(mods)
        if not known or any(m.problems and not m.mod_id for m in mods):
            return None
        names |= set(index)
        for mod in mods:
            if not mod.enabled:
                continue
            try:
                with zipfile.ZipFile(mod.path) as zf:
                    names |= _data_names(zf.namelist())
            except (OSError, zipfile.BadZipFile):
                return None
    elif ctx.config.server_type.content is not None:
        return None  # plugin servers keep extra worlds as folders of their own
    packs = world / "datapacks"
    if packs.is_dir():
        for pack in packs.iterdir():
            if pack.is_dir():
                data = pack / "data"
                names |= {d.name for d in data.iterdir() if d.is_dir()} if data.is_dir() else set()
            elif pack.suffix.lower() == ".zip":
                try:
                    with zipfile.ZipFile(pack) as zf:
                        names |= _data_names(zf.namelist())
                except (OSError, zipfile.BadZipFile):
                    return None
    return names


def _data_names(members: list[str]) -> set[str]:
    return {m.split("/")[1] for m in members if m.startswith("data/") and m.count("/") >= 2}


def scan(ctx: ServerContext) -> dict[str, Any]:
    """What could go, measured now (blocking: run it in a worker thread)."""
    empty_old_batches(ctx.config)
    base = ctx.config.server_dir
    if not base.is_dir():
        raise CleanupError("The server folder isn't there yet.")
    cutoff = time.time() - OLD_DAYS * 86400
    items = [_item(base, f, "old_log") for f in _old_files(base / "logs", cutoff, ("latest.log",))]
    reports = _old_files(base / "crash-reports", cutoff)
    reports += [f for f in _old_files(base, cutoff) if f.name.startswith("hs_err_pid")]
    items += [_item(base, f, "crash_report") for f in reports]
    items += [
        _item(base, p, "set_aside")
        for p in sorted(base.iterdir())
        if ".replaced-" in p.name and not p.is_symlink()
    ]
    dimensions_checked = ctx.config.server_type.edition == "java"
    for name in sorted(world_folders(base, ctx.config.server_type)):
        folder = base / name / "dimensions"
        if not folder.is_dir() or not dimensions_checked:
            continue
        known = _namespaces(ctx, base / name)
        if known is None:
            dimensions_checked = False
            continue
        items += [
            _item(base, d, "unused_dimension")
            for d in sorted(folder.iterdir())
            if d.is_dir() and not d.is_symlink() and d.name not in known
        ]
    return {
        "items": items,
        "total_bytes": sum(i["size_bytes"] for i in items),
        "dimensions_checked": dimensions_checked,
        "free_bytes": shutil.disk_usage(base).free,
        "bin": batches(ctx),
    }


def remove(ctx: ServerContext, paths: list[str], user: str = "system") -> dict[str, Any]:
    """Move the picked items into the bin as one batch. Only items the scan
    offers now can be picked, so nothing else in the folder can be moved."""
    ctx.require_free()
    offered = {i["path"]: i for i in scan(ctx)["items"]}
    unknown = [p for p in paths if p not in offered]
    if unknown:
        raise CleanupError(f"{unknown[0]} isn't on the clean-up list any more. Look again.")
    picked = [offered[p] for p in dict.fromkeys(paths)]
    if not picked:
        raise CleanupError("Pick something to clean up first.")
    if ctx.server.running and any(i["kind"] == "unused_dimension" for i in picked):
        raise CleanupError("Stop the server first: it may be using that dimension's files.")
    base = ctx.config.server_dir
    stamp = time.strftime("%Y%m%d-%H%M%S")
    batch = bin_dir(ctx.config) / stamp
    number = 2
    while batch.exists():
        batch = bin_dir(ctx.config) / f"{stamp}-{number}"
        number += 1
    moved, skipped = [], []
    for item in picked:
        source = safe_join(base, *item["path"].split("/"))
        target = batch.joinpath(*item["path"].split("/"))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            moved.append(item)
        except OSError as exc:
            # A log Minecraft still has open on Windows, for one.
            skipped.append({"path": item["path"], "reason": str(exc)})
    if moved:
        manifest = {"created_at": time.time(), "user": user, "items": moved}
        (batch / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
        ctx.db.audit("cleanup", user=user, detail=f"{len(moved)} item(s) to the bin")
    return {
        "batch": batch.name if moved else None,
        "moved": [i["path"] for i in moved],
        "skipped": skipped,
        "size_bytes": sum(i["size_bytes"] for i in moved),
        "bin_days": BIN_DAYS,
    }


def _manifest(batch: Path) -> dict[str, Any]:
    try:
        return json.loads((batch / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def batches(ctx: ServerContext) -> list[dict[str, Any]]:
    """What's in the bin, newest first."""
    root = bin_dir(ctx.config)
    if not root.is_dir():
        return []
    out = []
    for batch in sorted(root.iterdir(), reverse=True):
        data = _manifest(batch)
        if not batch.is_dir() or not data:
            continue
        out.append(
            {
                "batch": batch.name,
                "created_at": data.get("created_at"),
                "items": len(data.get("items") or []),
                "size_bytes": directory_size(batch),
                "empties_at": (data.get("created_at") or 0) + BIN_DAYS * 86400,
            }
        )
    return out


def _batch(ctx: ServerContext, name: str) -> Path:
    try:
        batch = safe_join(bin_dir(ctx.config), name)
    except Exception as exc:
        raise CleanupError("That clean-up isn't in the bin.") from exc
    if not batch.is_dir() or not _manifest(batch):
        raise CleanupError("That clean-up isn't in the bin any more.")
    return batch


def undo(ctx: ServerContext, name: str, user: str = "system") -> dict[str, Any]:
    """Put a batch back where it was. Something made again at the same place
    since (a new log with the same name) is never written over: that item
    stays in the bin and is listed."""
    ctx.require_free()
    batch = _batch(ctx, name)
    base = ctx.config.server_dir
    back, left = [], []
    for item in _manifest(batch).get("items") or []:
        parts = item["path"].split("/")
        source, target = batch.joinpath(*parts), safe_join(base, *parts)
        if not source.exists():
            continue
        if target.exists():
            left.append(item["path"])
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        back.append(item["path"])
    if not left:
        shutil.rmtree(batch, ignore_errors=True)
    ctx.db.audit("cleanup_undo", user=user, detail=f"{len(back)} item(s) put back")
    return {"put_back": back, "left_in_bin": left}


def empty(ctx: ServerContext, name: str | None = None, user: str = "system") -> dict[str, Any]:
    """Empty one batch, or the whole bin, for good. This frees the space."""
    targets = (
        [_batch(ctx, name)] if name else [bin_dir(ctx.config) / b["batch"] for b in batches(ctx)]
    )
    freed = 0
    for batch in targets:
        freed += directory_size(batch)
        shutil.rmtree(batch, ignore_errors=True)
    ctx.db.audit("cleanup_empty", user=user, detail=f"{len(targets)} batch(es)")
    return {"emptied": len(targets), "freed_bytes": freed}


def empty_old_batches(config: Any) -> int:
    """Batches older than BIN_DAYS go for good. Also run by the weekly log
    clean-up schedule, so the bin empties even when nobody opens the page."""
    root = bin_dir(config)
    if not root.is_dir():
        return 0
    cutoff = time.time() - BIN_DAYS * 86400
    emptied = 0
    for batch in root.iterdir():
        if batch.is_dir() and (_manifest(batch).get("created_at") or 0) < cutoff:
            shutil.rmtree(batch, ignore_errors=True)
            log.info("emptied clean-up %s", batch.name)
            emptied += 1
    return emptied
