"""Server files: look through a server's folder, download a file from it,
upload a file into one of its folders. Owner only (files.browse).

Only inside the server folder: every path from the apps is checked by
security/paths.py (no "..", no drive letter, no link or junction that leads
somewhere else). Downloads and uploads have size limits. An upload never
replaces a file that is already there, and its name follows the same rules
as every other file this agent writes (no .exe, .bat and the like).
Editing stays with Edit settings (agent/configfiles.py).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .security.paths import (
    PathSafetyError,
    assert_not_symlink,
    check_archive_member,
    safe_join,
)

MAX_ENTRIES = 1000
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024  # 1 GB: a whole world goes through World > Download
MAX_UPLOAD_BYTES = 256 * 1024 * 1024


def _inside(ctx, relative: str) -> tuple[Path, Path]:
    """The server folder, and ``relative`` (with / between folders) inside it."""
    base = Path(ctx.config.server_dir).resolve()
    text = (relative or "").strip().strip("/")
    if not text:
        return base, base
    try:
        target = check_archive_member(base, text)
    except PathSafetyError:
        raise PathSafetyError("That path leads outside the server folder.") from None
    # Every step on the way, so a link or junction is never walked through.
    step = base
    for part in Path(text.replace("\\", "/")).parts:
        step = step / part
        assert_not_symlink(step)
    return base, target


def _relative(base: Path, path: Path) -> str:
    return path.relative_to(base).as_posix() if path != base else ""


def listing(ctx, relative: str = "") -> dict[str, Any]:
    """Folders first, then files, each with its size and last change."""
    base, folder = _inside(ctx, relative)
    if not folder.is_dir():
        raise PathSafetyError(f"{relative} isn't a folder in this server.")
    entries: list[dict[str, Any]] = []
    with os.scandir(folder) as found:
        for entry in found:
            try:
                if entry.is_symlink():
                    continue
                is_folder = entry.is_dir(follow_symlinks=False)
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            entries.append(
                {
                    "name": entry.name,
                    "folder": is_folder,
                    "size": None if is_folder else info.st_size,
                    "changed": info.st_mtime,
                }
            )
    entries.sort(key=lambda e: (not e["folder"], e["name"].lower()))
    here = _relative(base, folder)
    return {
        "path": here,
        "parent": None if folder == base else _relative(base, folder.parent),
        "entries": entries[:MAX_ENTRIES],
        "truncated": len(entries) > MAX_ENTRIES,
    }


def for_download(ctx, relative: str) -> Path:
    """The file to send, after the checks."""
    _base, path = _inside(ctx, relative)
    if not path.is_file():
        raise PathSafetyError(f"{relative} isn't a file in this server.")
    if path.stat().st_size > MAX_DOWNLOAD_BYTES:
        raise PathSafetyError(
            f"{path.name} is bigger than 1 GB. For a whole world use World > Download."
        )
    return path


def save_upload(ctx, relative: str, name: str, data: bytes) -> dict[str, Any]:
    """Writes ``data`` as ``name`` in the folder ``relative``. Never over a
    file that's already there."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise PathSafetyError("That file is bigger than 256 MB, the most that can be uploaded.")
    base, folder = _inside(ctx, relative)
    if not folder.is_dir():
        raise PathSafetyError(f"{relative} isn't a folder in this server.")
    target = safe_join(folder, name)
    try:
        with open(target, "xb") as out:  # "x": fails if the file is there
            try:
                out.write(data)
            except OSError:
                out.close()
                target.unlink(missing_ok=True)
                raise
    except FileExistsError:
        raise PathSafetyError(
            f"There's already a {name} in that folder. Rename your file and upload it again."
        ) from None
    return {"path": _relative(base, target), "name": name, "size": len(data)}
