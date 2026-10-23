"""Updates on a moderator's PC: it has no agent, so nothing else updates its
copy of the desktop app. The owner's PC is updated by its agent
(agent/updates.py), so only a copy for connecting only checks here.

Once a day it asks GitHub for this app's latest release (through the safe
downloader) and offers it only when it is newer than this copy, never an
older one. "Update now" checks the signed update.json with the key built
into this copy, downloads the setup file and refuses it unless its SHA-256
and size match the signed file. Then the app closes and runs that setup for
this Windows user (no admin), which opens the app again (--after-update).
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import time
from pathlib import Path
from typing import Any

from agent import appinfo, downloads, signing, updates

from .agent import AgentError
from .words import text

CHECK_EVERY = 24 * 3600


def wanted() -> bool:
    record = appinfo.install_record()
    return bool(record and record.get("connect_only")) and signing.has_key()


def due(checked_at: Any, now: float | None = None) -> bool:
    try:
        last = float(checked_at or 0)
    except (TypeError, ValueError):
        last = 0.0
    return (now or time.time()) - last >= CHECK_EVERY


def _failed(exc: Exception) -> AgentError:
    return AgentError(text("desktop.self_update.failed", why=exc))


def newer_release() -> updates.Release | None:
    """The latest release, when it is newer than this copy."""
    try:
        release = updates.parse_release(asyncio.run(downloads.fetch_json(updates.LATEST_URL)))
    except (downloads.DownloadError, updates.UpdateError) as exc:
        raise _failed(exc) from exc
    return release if updates.is_newer(release.version) else None


def download(release: updates.Release) -> Path:
    """The release's setup file, checked against the signed update.json."""
    record = appinfo.install_record() or {}
    try:
        signed = asyncio.run(updates.fetch_signed(release.version))
        spec = downloads.FileSpec(
            url=release.setup_url,
            # The safe downloader never writes an .exe; the checked bytes
            # become the setup file below.
            name=updates.download_name(release.version),
            sha256=signed.sha256,
            size=signed.size,
            allow_unverified=False,
            max_bytes=updates.MAX_SETUP_BYTES,
        )
        folder = Path(record["data_root"]) / "updates" / release.version
        shutil.rmtree(folder.parent, ignore_errors=True)  # the last update's setup file
        fetched = asyncio.run(downloads.download(spec, folder)).path
    except (downloads.DownloadError, updates.UpdateError, KeyError) as exc:
        raise _failed(exc) from exc
    data = fetched.read_bytes()
    fetched.unlink(missing_ok=True)
    if hashlib.sha256(data).hexdigest() != signed.sha256 or len(data) != signed.size:
        raise _failed(ValueError("the download doesn't match the signed release"))
    setup = folder / updates.asset_name(release.version)
    setup.write_bytes(data)
    return setup
