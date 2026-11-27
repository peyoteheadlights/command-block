"""Older logs: Minecraft's own files in the server's logs folder
(latest.log and the zipped 2026-11-20-1.log.gz ones), read-only.

Only names in that one folder are read, checked by security/paths.py, and
a big file is cut after its first lines (MAX_BYTES, MAX_LINES).
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path

from .security.paths import PathSafetyError, assert_not_symlink, safe_existing

NAME_RE = re.compile(r"^[\w.-]+\.log(\.gz)?$")
MAX_FILES = 300
MAX_BYTES = 4 * 1024 * 1024  # read at most this much of one file, unzipped
MAX_LINES = 5000  # and send at most this many of its last lines


def folder(ctx) -> Path:
    return Path(ctx.config.server_dir) / "logs"


def listing(ctx) -> list[dict]:
    """Newest first: name, size on disk, last changed."""
    place = folder(ctx)
    if not place.is_dir():
        return []
    files = [p for p in place.iterdir() if NAME_RE.match(p.name) and p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {"name": p.name, "size": p.stat().st_size, "changed": p.stat().st_mtime}
        for p in files[:MAX_FILES]
    ]


def read(ctx, name: str) -> dict:
    """The file's lines, and whether later ones were left out."""
    if not NAME_RE.match(name):
        raise PathSafetyError("That isn't a log file name.")
    path = assert_not_symlink(safe_existing(folder(ctx), name, {".log", ".gz"}))
    opener = gzip.open if name.endswith(".gz") else open
    try:
        with opener(path, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise PathSafetyError(f"{name} couldn't be read: {exc}") from exc
    cut = len(data) > MAX_BYTES
    lines = data[:MAX_BYTES].decode("utf-8", errors="replace").splitlines()
    cut = cut or len(lines) > MAX_LINES
    return {"name": name, "lines": lines[:MAX_LINES], "cut": cut}
