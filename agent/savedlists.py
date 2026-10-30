"""Saved copies of the lists the app reads from official sites.

Every version list, loader or build list, Mojang version file and
Modrinth version list is kept in the data folder (``saved-lists``) with the
time it was read, once the caller's own check has said the answer is good.
When the site can't be reached, or sends something broken, the saved copy
is used instead and its age is noted, so the apps can say "From 3 days ago,
couldn't reach PaperMC". With no saved copy the error stands: a list is
never made up.

Only lists are saved. Files (server jars, mods) always come from the
official site and are checked against their checksum, as before.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from . import downloads
from .downloads import DownloadError

log = logging.getLogger("msc.savedlists")

FOLDER = "saved-lists"
# The names people know each site by, for "couldn't reach PaperMC".
SITE_NAMES = {
    "piston-meta.mojang.com": "Mojang",
    "meta.fabricmc.net": "Fabric",
    "meta.quiltmc.org": "Quilt",
    "maven.minecraftforge.net": "Forge",
    "maven.neoforged.net": "NeoForge",
    "fill.papermc.io": "PaperMC",
    "api.purpurmc.org": "Purpur",
    "api.modrinth.com": "Modrinth",
}

_root: Path | None = None
# The saved copies used while working out one answer (see watch()).
_used: ContextVar[list[dict[str, Any]] | None] = ContextVar("saved_lists_used", default=None)


def configure(data_dir: Path) -> None:
    """Called by the agent at start: copies live in its data folder."""
    global _root
    _root = Path(data_dir) / FOLDER


def root() -> Path:
    if _root is not None:
        return _root
    from .config import default_data_root

    return default_data_root() / FOLDER


def _path(key: str) -> Path:
    return root() / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:32] + ".json")


def save(key: str, data: Any) -> None:
    """Keep ``data`` as the last good answer for ``key``. Written to a
    temporary file first, so a crash never leaves half a copy."""
    path = _path(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.part")
        temp.write_text(
            json.dumps({"key": key, "saved_at": time.time(), "data": data}), encoding="utf-8"
        )
        os.replace(temp, path)
    except OSError:
        log.warning("couldn't save a copy of %s", key, exc_info=True)


def load(key: str) -> tuple[Any, float] | None:
    """The saved answer for ``key`` and when it was read, or None."""
    try:
        saved = json.loads(_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict) or saved.get("key") != key:
        return None
    return saved.get("data"), float(saved.get("saved_at") or 0)


@contextmanager
def watch() -> Iterator[list[dict[str, Any]]]:
    """Collect every saved copy used inside the block: [{"site", "saved_at"}]."""
    used: list[dict[str, Any]] = []
    token = _used.set(used)
    try:
        yield used
    finally:
        _used.reset(token)


def fall_back(key: str, host: str, error: Exception) -> Any:
    """The saved answer for ``key``, noted as used, or ``error`` again."""
    saved = load(key)
    if saved is None:
        raise error
    data, saved_at = saved
    log.info("using the copy of %s saved %.0f s ago: %s", key, time.time() - saved_at, error)
    used = _used.get()
    if used is not None:
        used.append({"site": SITE_NAMES.get(host, host), "saved_at": saved_at})
    return data


async def fetch_json(url: str, check: Callable[[Any], Any] | None = None) -> Any:
    """``downloads.fetch_json``, kept when ``check`` passes, and the saved
    copy when the site can't be reached or sends something ``check`` fails."""
    try:
        data = await downloads.fetch_json(url)
        if check is not None and not check(data):
            raise DownloadError(
                f"{downloads.source_name(url)} sent an answer that couldn't be read."
            )
    except DownloadError as exc:
        return fall_back(url, downloads.source_name(url), exc)
    save(url, data)
    return data


async def fetch_text(url: str, check: Callable[[str], Any] | None = None) -> str:
    """The same for a text answer (the Maven version lists)."""
    try:
        text = await downloads.fetch_text(url)
        if check is not None:
            check(text)  # raises when the answer is broken
    except DownloadError as exc:
        return str(fall_back(url, downloads.source_name(url), exc))
    save(url, text)
    return text


def age_words(seconds: float) -> str:
    minutes = max(int(seconds // 60), 1)
    if minutes < 60:
        return "1 minute" if minutes == 1 else f"{minutes} minutes"
    hours = minutes // 60
    if hours < 48:
        return "1 hour" if hours == 1 else f"{hours} hours"
    return f"{hours // 24} days"


def problem(
    used: list[dict[str, Any]], now: float | None = None
) -> tuple[float | None, str | None]:
    """(saved_at of the oldest copy used, the sentence the pickers show)."""
    if not used:
        return None, None
    oldest = min(used, key=lambda u: u["saved_at"])
    age = (time.time() if now is None else now) - oldest["saved_at"]
    return oldest["saved_at"], f"From {age_words(age)} ago, couldn't reach {oldest['site']}."
