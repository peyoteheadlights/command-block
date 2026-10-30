"""The signed notes file: safety warnings that reach owners without a new
release of the app.

Some things no official site publishes: "this mod file is infected", or
"Minecraft versions X to Y have a hole like Log4j". They go in one small
file, ``data/catalog.json`` in the repo, published on the ``data``
pre-release with its signature (``catalog.json.sig``). The agent reads it at
start and once a day. What it may hold:

  * ``advisories``: a server type and version range, what to do, a link.
    Shown as Recommendations; the link is only ever shown, never opened or
    fetched by the app.
  * ``known_bad_files``: SHA-512, name and why. Installing one is refused,
    and one already on a server is flagged.
  * ``java``: extra Minecraft-to-Java rows, only newer than the built-in
    table's newest.

Data can only add warnings or blocks. It never runs anything and never
removes a check. The file is refused as a whole, and the last good one
kept, when anything is wrong: the signature (its own data key, separate
from the update key), a serial lower than one already seen, over 256 KB, a
field of the wrong type or length, a link that isn't https. Keys it doesn't
know are ignored, so a newer file still works on an older app; an entry
marked ``min_app`` above this version is skipped and the status says to
update the app. A file past its ``expires`` date keeps its warnings,
labelled with their age.

With no good file the apps say "Couldn't check for safety notes", never
"no problems found".
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import datetime as dt
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import __version__, downloads, savedlists

log = logging.getLogger("msc.notes")

SCHEMA = 1
MAX_BYTES = 256 * 1024
MAX_ENTRIES = 1000
# The public half of the data key (Ed25519, base64), separate from the
# update key in agent/signing.py: a leaked data key can at worst show false
# warnings, never push an app update. Until the real key is made this is a
# test key whose private half was never kept, so every notes file is refused.
DATA_PUBLIC_KEY = "vZnoR6H+G814CoAo7RhKmRFQDw4XBXewsIXg7USbTME="
# Room for the next key, so the key can be changed without a release.
DATA_NEXT_KEY = ""
NOTES_URL = f"https://github.com/{downloads.REPO}/releases/download/data/catalog.json"
SIGNATURE_URL = NOTES_URL + ".sig"
CHECK_EVERY = 24 * 3600
FIRST_CHECK_DELAY = 60
SERIAL_KEY = "notes:serial"
FOLDER = "notes"
NO_NOTES = "Couldn't check for safety notes."
UPDATE_APP = "Update Command Block to get the newest notes."
TYPES = ("vanilla", "fabric", "quilt", "forge", "neoforge", "paper", "purpur", "bedrock")

# SHA-512 -> entry, from the last good notes file. Read by the mod and
# plugin installer (agent/mods/manager.py) and modpack import.
BAD_FILES: dict[str, dict[str, Any]] = {}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SHA512 = re.compile(r"^[0-9a-f]{128}$")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+\-]{0,63}$")
_APP_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
# Plain text: no control characters and nothing that could be read as markup.
_NOT_PLAIN = re.compile(r"[\x00-\x1f\x7f<>]")


class NotesRefused(ValueError):
    """Why a notes file was refused, in one plain sentence."""


@dataclass
class Notes:
    serial: int
    issued: str
    expires: str
    expired: bool
    advisories: list[dict[str, Any]] = field(default_factory=list)
    known_bad_files: list[dict[str, Any]] = field(default_factory=list)
    java: list[dict[str, Any]] = field(default_factory=list)
    # Entries marked for a newer app than this one.
    skipped: int = 0


# ---------------------------------------------------------------- the loader
def _text(entry: dict, key: str, limit: int) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not 0 < len(value) <= limit or _NOT_PLAIN.search(value):
        raise NotesRefused(f"The notes file has a bad '{key}'.")
    return value


def _match(entry: dict, key: str, pattern: re.Pattern) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not pattern.match(value):
        raise NotesRefused(f"The notes file has a bad '{key}'.")
    return value


def _link(entry: dict, key: str) -> str:
    value = _text(entry, key, 300)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or " " in value:
        raise NotesRefused("A link in the notes file isn't an https link.")
    return value


def _app(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _entries(body: dict, key: str, read, app: str) -> tuple[list[dict[str, Any]], int]:
    rows = body.get(key, [])
    if not isinstance(rows, list) or len(rows) > MAX_ENTRIES:
        raise NotesRefused(f"The notes file's '{key}' isn't a list.")
    kept, skipped = [], 0
    for row in rows:
        if not isinstance(row, dict):
            raise NotesRefused(f"The notes file's '{key}' holds something that isn't an entry.")
        if "min_app" in row and _app(_match(row, "min_app", _APP_VERSION)) > _app(app):
            skipped += 1
            continue
        kept.append(read(row))
    return kept, skipped


def _advisory(row: dict) -> dict[str, Any]:
    types = row.get("types")
    if not isinstance(types, list) or not types or not all(t in TYPES for t in types):
        raise NotesRefused("The notes file has a bad 'types'.")
    return {
        "id": _match(row, "id", _ID),
        "types": list(types),
        "from": _match(row, "from", _VERSION),
        "to": _match(row, "to", _VERSION),
        "title": _text(row, "title", 120),
        "what_to_do": _text(row, "what_to_do", 500),
        "link": _link(row, "link"),
    }


def _bad_file(row: dict) -> dict[str, Any]:
    return {
        "sha512": _match(row, "sha512", _SHA512),
        "name": _text(row, "name", 120),
        "why": _text(row, "why", 300),
    }


def _java_row(row: dict) -> dict[str, Any]:
    java = row.get("java")
    if not isinstance(java, int) or isinstance(java, bool) or not 8 <= java <= 99:
        raise NotesRefused("The notes file has a bad 'java'.")
    return {"minecraft": _match(row, "minecraft", _VERSION), "java": java}


def _date(body: dict, key: str) -> dt.date:
    value = _match(body, key, _DATE)
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise NotesRefused(f"The notes file has a bad '{key}'.") from exc


def parse(document: bytes, highest_serial: int, now: float, app_version: str) -> Notes:
    """Check a notes file's contents (not its signature). The repo's own
    data/catalog.json is checked with this in the tests."""
    if len(document) > MAX_BYTES:
        raise NotesRefused("The notes file is too big to be one of ours.")
    try:
        body = json.loads(document.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise NotesRefused("The notes file couldn't be read.") from exc
    if not isinstance(body, dict) or body.get("schema") != SCHEMA:
        raise NotesRefused("The notes file isn't in a shape this app reads.")
    serial = body.get("serial")
    if not isinstance(serial, int) or isinstance(serial, bool) or serial < 1:
        raise NotesRefused("The notes file has a bad 'serial'.")
    if serial < highest_serial:
        raise NotesRefused(
            f"The notes file is older (number {serial}) than one already seen "
            f"(number {highest_serial}), so it was refused."
        )
    issued, expires = _date(body, "issued"), _date(body, "expires")
    advisories, a = _entries(body, "advisories", _advisory, app_version)
    bad, b = _entries(body, "known_bad_files", _bad_file, app_version)
    java, c = _entries(body, "java", _java_row, app_version)
    today = dt.datetime.fromtimestamp(now, dt.UTC).date()
    return Notes(
        serial=serial,
        issued=issued.isoformat(),
        expires=expires.isoformat(),
        expired=today > expires,
        advisories=advisories,
        known_bad_files=bad,
        java=java,
        skipped=a + b + c,
    )


def check(
    document: bytes,
    signature: str,
    keys: list[str],
    highest_serial: int,
    now: float,
    app_version: str,
) -> Notes:
    """The one way in: checked notes, or NotesRefused with the reason.
    Pure: no network, no disk."""
    if len(document) > MAX_BYTES:
        raise NotesRefused("The notes file is too big to be one of ours.")
    try:
        raw = base64.b64decode(signature.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise NotesRefused("The notes file's signature isn't readable.") from exc
    for key in (k for k in keys if k.strip()):
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(raw, document)
            break
        except (InvalidSignature, ValueError):
            continue
    else:
        raise NotesRefused("The notes file isn't signed with this app's data key.")
    return parse(document, highest_serial, now, app_version)


# ---------------------------------------------------------------- the agent's side
def sha512_of(path: Path) -> str:
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha512").hexdigest()


def known_bad(data: bytes) -> dict[str, Any] | None:
    """The known bad file entry these bytes match, or None."""
    if not BAD_FILES:
        return None
    return BAD_FILES.get(hashlib.sha512(data).hexdigest())


class SafetyNotes:
    """Fetches the notes file at start and once a day, keeps the last good
    one in the data folder, and says what it knows."""

    def __init__(self, core):
        self.core = core
        self.folder = Path(core.config.data_dir) / FOLDER
        self.notes: Notes | None = None
        self.fetched_at: float | None = None  # when the last good file arrived
        self.checked_at: float | None = None
        self.problem: str | None = None  # why the newest try didn't give a good file
        self.refused = False  # a file came but didn't check out
        # server id -> known bad files found on it [{"filename", "name", "why"}]
        self.bad_installed: dict[str, list[dict[str, Any]]] = {}
        self._task: asyncio.Task | None = None
        self._load_saved()

    # ------------------------------------------------------------ saved copy
    def _keys(self) -> list[str]:
        return [DATA_PUBLIC_KEY, DATA_NEXT_KEY]

    def _load_saved(self) -> None:
        try:
            document = (self.folder / "catalog.json").read_bytes()
            signature = (self.folder / "catalog.json.sig").read_text("utf-8")
            meta = json.loads((self.folder / "meta.json").read_text("utf-8"))
        except (OSError, ValueError):
            return
        try:
            notes = check(
                document,
                signature,
                self._keys(),
                int(self.core.db.get_setting(SERIAL_KEY, 0) or 0),
                time.time(),
                __version__,
            )
        except NotesRefused as exc:
            self.problem, self.refused = str(exc), True
            return
        self.fetched_at = float(meta.get("fetched_at") or 0) or None
        self._apply(notes)

    def _save(self, document: bytes, signature: str, etag: str | None) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        for name, data in (
            ("catalog.json", document),
            ("catalog.json.sig", signature.encode("utf-8")),
            ("meta.json", json.dumps({"etag": etag, "fetched_at": self.fetched_at}).encode()),
        ):
            temp = self.folder / f".{name}.part"
            temp.write_bytes(data)
            os.replace(temp, self.folder / name)

    def _etag(self) -> str | None:
        try:
            return json.loads((self.folder / "meta.json").read_text("utf-8")).get("etag")
        except (OSError, ValueError):
            return None

    # ------------------------------------------------------------ checking
    async def check_now(self) -> dict[str, Any]:
        self.checked_at = time.time()
        try:
            document, etag = await downloads.fetch_if_changed(
                NOTES_URL, self._etag() if self.notes else None, MAX_BYTES
            )
            if document is None:  # unchanged since the good copy kept here
                self.problem = None
                return self.status()
            signature = await downloads.fetch_text(SIGNATURE_URL, max_bytes=1024)
            notes = check(
                document,
                signature,
                self._keys(),
                int(self.core.db.get_setting(SERIAL_KEY, 0) or 0),
                time.time(),
                __version__,
            )
        except (downloads.DownloadError, NotesRefused) as exc:
            self.problem, self.refused = str(exc), isinstance(exc, NotesRefused)
            log.info("safety notes not updated: %s", exc)
            return self.status()
        self.problem, self.refused = None, False
        self.fetched_at = time.time()
        self.core.db.set_setting(SERIAL_KEY, notes.serial)
        await asyncio.to_thread(self._save, document, signature, etag)
        self._apply(notes)
        await self.scan_installed()
        return self.status()

    def _apply(self, notes: Notes) -> None:
        from .minecraft import java
        from .minecraft.java import MINECRAFT_JAVA_REQUIREMENTS, parse_minecraft

        self.notes = notes
        BAD_FILES.clear()
        BAD_FILES.update({row["sha512"]: row for row in notes.known_bad_files})
        newest = max(threshold for threshold, _ in MINECRAFT_JAVA_REQUIREMENTS)
        rows = [(parse_minecraft(r["minecraft"]), r["java"]) for r in notes.java]
        # Data can only add rows newer than the built-in table's newest.
        java.NOTES_ROWS[:] = [(v, j) for v, j in rows if v and v > newest]

    async def scan_installed(self) -> None:
        """Look for known bad files already on each server. Only hashes
        anything when the notes list some."""
        found: dict[str, list[dict[str, Any]]] = {}
        if BAD_FILES:
            for server_id, ctx in list(self.core.servers.items()):
                if not ctx.config.server_type.content:
                    continue
                found[server_id] = await asyncio.to_thread(self._scan, ctx)
        self.bad_installed = found

    @staticmethod
    def _scan(ctx) -> list[dict[str, Any]]:
        hits = []
        for mod in ctx.mods.scan():
            try:
                entry = BAD_FILES.get(sha512_of(mod.path))
            except OSError:
                continue
            if entry:
                hits.append({"filename": mod.filename, "name": entry["name"], "why": entry["why"]})
        return hits

    async def _loop(self) -> None:
        await self.scan_installed()
        await asyncio.sleep(FIRST_CHECK_DELAY)
        while True:
            try:
                await self.check_now()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.debug("safety notes check failed", exc_info=True)
            await asyncio.sleep(CHECK_EVERY)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="safety-notes")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    # ------------------------------------------------------------ what it knows
    def age_label(self) -> str | None:
        """ "Notes from 40 days ago, couldn't get newer ones." for an expired
        file, None otherwise."""
        if not self.notes or not self.notes.expired:
            return None
        issued = dt.datetime.fromisoformat(self.notes.issued).replace(tzinfo=dt.UTC)
        age = savedlists.age_words(time.time() - issued.timestamp())
        return f"Notes from {age} ago, couldn't get newer ones."

    def status(self) -> dict[str, Any]:
        notes = self.notes
        if notes is None:
            state, message = ("refused" if self.refused else "none"), NO_NOTES
        elif notes.expired:
            state, message = "expired", self.age_label() or NO_NOTES
        else:
            state, message = "good", f"Safety notes number {notes.serial}, from {notes.issued}."
        return {
            "state": state,
            "message": message,
            "reason": self.problem,
            "serial": notes.serial if notes else None,
            "issued": notes.issued if notes else None,
            "expires": notes.expires if notes else None,
            "fetched_at": self.fetched_at,
            "checked_at": self.checked_at,
            "needs_newer_app": bool(notes and notes.skipped),
            "update_app": UPDATE_APP if notes and notes.skipped else None,
            "advisories": len(notes.advisories) if notes else 0,
            "known_bad_files": len(notes.known_bad_files) if notes else 0,
        }
