"""Signed account records: the owner, moderators, signed-in devices and the
PCs of one owner, as the PCs share them (agent/yourpcs.py).

Every record is a row of ``sync_records``: its kind and key, its body (JSON),
when it was written (a hybrid logical clock, below), which PC wrote it,
whether it is a "removed" marker, and an HMAC-SHA256 signature over all of
that. The signing key is shared by the joined PCs and kept encrypted for
the agent's Windows account (DPAPI), next to this PC's sync key.

A row whose signature doesn't match is never used: the sign-in it was for
is refused and the owner is alerted. Like a Java class with a private
``verify`` that every getter calls first.

The hybrid logical clock (HLC) is the wall clock in milliseconds plus a
counter, plus the PC's id: it only moves forward, and it moves past any
newer time another PC sent, so "the newest change wins" is fair even when
two PCs' clocks don't agree.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("msc.records")

KEY_FILE = "account-keys.bin"
# Kinds whose "removed" marker is final: a newer copy never brings a removed
# moderator, sign-in or PC back.
STICKY = {"account", "session", "pc"}
KINDS = {"owner", "owner_reset", "account", "grants", "session", "pc"}
# The Discord bot across the PCs (agent/discord/botpc.py).
KINDS |= {"bot", "bot_settings", "bot_link", "bot_news"}


def canonical(body: dict[str, Any]) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


# ------------------------------------------------------------------ the keys
def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows' DPAPI: only the same Windows account on the same PC can
    decrypt what it encrypted."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    given = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    no_prompt = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        done = crypt32.CryptProtectData(
            ctypes.byref(given), "Command Block", None, None, None, no_prompt, ctypes.byref(out)
        )
    else:
        done = crypt32.CryptUnprotectData(
            ctypes.byref(given), None, None, None, None, no_prompt, ctypes.byref(out)
        )
    if not done:
        raise OSError("DPAPI refused")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)  # type: ignore[attr-defined]


def read_keys(path: Path) -> dict[str, bytes] | None:
    """The saved keys, or None when the file is missing or can't be read
    (another PC's DPAPI, a damaged file)."""
    try:
        raw = path.read_bytes()
        if os.name == "nt":
            raw = _dpapi(raw, protect=False)
        found = json.loads(raw)
        keys = {name: base64.b64decode(value) for name, value in found.items()}
    except (OSError, ValueError, TypeError):
        return None
    if len(keys.get("signing", b"")) != 32 or len(keys.get("sync", b"")) != 32:
        return None
    return keys


def write_keys(path: Path, keys: dict[str, bytes]) -> None:
    from .certs import _restrict_file

    raw = json.dumps({k: base64.b64encode(v).decode() for k, v in keys.items()}).encode()
    if os.name == "nt":
        raw = _dpapi(raw, protect=True)
    tmp = path.with_suffix(".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(raw)
    _restrict_file(tmp)
    tmp.replace(path)


def read_secrets(path: Path) -> dict[str, str]:
    """A small file of named secrets (a panel's key, a store's key),
    encrypted with DPAPI on Windows. Empty when it's missing or can't be read."""
    try:
        raw = path.read_bytes()
        if os.name == "nt":
            raw = _dpapi(raw, protect=False)
        found = json.loads(raw)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("%s couldn't be read", path.name)
        return {}
    return {str(k): str(v) for k, v in found.items()}


def write_secrets(path: Path, secrets_: dict[str, str]) -> None:
    from .certs import _restrict_file

    raw = json.dumps(secrets_).encode()
    if os.name == "nt":
        raw = _dpapi(raw, protect=True)
    tmp = path.with_suffix(".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(raw)
    _restrict_file(tmp)
    tmp.replace(path)


# ------------------------------------------------------------------ the clock
class Clock:
    def __init__(self, pc: str, newest: str | None = None) -> None:
        self.pc = pc
        self.ms, self.count = 0, 0
        if newest:
            self.observe(newest)

    @staticmethod
    def parse(hlc: str) -> tuple[int, int]:
        ms, count, _pc = hlc.split(".", 2)
        return int(ms), int(count)

    def now(self) -> str:
        wall = int(time.time() * 1000)
        if wall > self.ms:
            self.ms, self.count = wall, 0
        else:
            self.count += 1
        return f"{self.ms:013d}.{self.count:05d}.{self.pc}"

    def observe(self, hlc: str) -> None:
        try:
            ms, count = self.parse(hlc)
        except ValueError:
            return
        if (ms, count) > (self.ms, self.count):
            self.ms, self.count = ms, count


# ------------------------------------------------------------------ the store
class Records:
    """The signed records in this PC's database. ``changed`` is called after
    each change made here or taken from another PC (the sync pushes it on).
    ``tampered`` is called with a sentence when a row fails its check."""

    def __init__(self, db, data_dir: Path) -> None:
        self.db = db
        self.path = Path(data_dir) / KEY_FILE
        self._lock = threading.RLock()
        self.changed: list[Callable[[], None]] = []
        self.applied: dict[str, Callable[[dict[str, Any], dict[str, Any] | None], None]] = {}
        self.tampered: Callable[[str], None] = lambda words: None
        self._told: set[tuple[str, str]] = set()
        pc = db.get_setting("pc_id")
        if not pc:
            pc = secrets.token_hex(8)
            db.set_setting("pc_id", pc)
        self.pc: str = pc
        newest = db.query_one("SELECT MAX(hlc) AS h FROM sync_records")
        self.clock = Clock(pc, newest["h"] if newest else None)
        self.keys = self._load_keys()

    def _load_keys(self) -> dict[str, bytes]:
        keys = read_keys(self.path)
        if keys is not None:
            return keys
        keys = {"signing": secrets.token_bytes(32), "sync": secrets.token_bytes(32)}
        had_records = self.db.query_one("SELECT COUNT(*) AS n FROM sync_records")
        write_keys(self.path, keys)
        if had_records and had_records["n"]:
            # A data folder moved or restored onto another Windows account
            # or PC: DPAPI can't open the old key. The records are signed
            # again with a new one, and the owner told (docs/security.md).
            self._sign_all(keys["signing"])
            log.warning("the account key couldn't be read, so a new one was made")
            self.db.set_setting("records_key_remade", time.time())
        return keys

    # -- signing ----------------------------------------------------------
    @staticmethod
    def _signature(key: bytes, row: dict[str, Any]) -> str:
        message = "\n".join(
            [row["kind"], row["key"], row["body"], row["hlc"], row["pc"], str(int(row["removed"]))]
        )
        return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()

    def _ok(self, row: dict[str, Any]) -> bool:
        return hmac.compare_digest(
            self._signature(self.keys["signing"], row), str(row.get("sig") or "")
        )

    def _sign_all(self, key: bytes) -> None:
        with self._lock:
            for row in self.db.query("SELECT * FROM sync_records"):
                self.db.execute(
                    "UPDATE sync_records SET sig = ? WHERE kind = ? AND key = ?",
                    (self._signature(key, row), row["kind"], row["key"]),
                )

    def set_signing_key(self, key: bytes) -> None:
        """A joined PC takes the account's key, or a removed one makes its
        own: the rows kept are signed again with it."""
        with self._lock:
            self.keys = {**self.keys, "signing": key}
            write_keys(self.path, self.keys)
            self._sign_all(key)

    def set_key(self, name: str, value: bytes | None) -> None:
        """Keep (or forget) one more key in the encrypted key file."""
        with self._lock:
            self.keys = {k: v for k, v in self.keys.items() if k != name}
            if value is not None:
                self.keys[name] = value
            write_keys(self.path, self.keys)

    def sync_key_hash(self) -> str:
        return hashlib.sha256(self.keys["sync"]).hexdigest()

    # -- reading -----------------------------------------------------------
    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {**row, "body": json.loads(row["body"]), "removed": bool(row["removed"])}

    def raw(self, kind: str, key: str) -> dict[str, Any] | None:
        return self.db.query_one(
            "SELECT * FROM sync_records WHERE kind = ? AND key = ?", (kind, key)
        )

    def get(self, kind: str, key: str, removed: bool = False) -> dict[str, Any] | None:
        """The record, checked; None when it's missing, removed (unless
        ``removed``) or fails its signature (the owner is told once)."""
        row = self.raw(kind, key)
        if row is None:
            return None
        if not self._ok(row):
            self._tell(kind, key)
            return None
        record = self._public(row)
        if record["removed"] and not removed:
            return None
        return record

    def all(self, kind: str, removed: bool = False) -> list[dict[str, Any]]:
        out = []
        for row in self.db.query("SELECT * FROM sync_records WHERE kind = ? ORDER BY key", (kind,)):
            if not self._ok(row):
                self._tell(kind, row["key"])
                continue
            if row["removed"] and not removed:
                continue
            out.append(self._public(row))
        return out

    def _tell(self, kind: str, key: str) -> None:
        if (kind, key) in self._told:
            return
        self._told.add((kind, key))
        log.warning("account record %s %s failed its signature check", kind, key[:16])
        self.tampered(kind)

    def since(self, seq: int) -> list[dict[str, Any]]:
        """Rows changed here after ``seq``, as sent to another PC."""
        rows = self.db.query("SELECT * FROM sync_records WHERE seq > ? ORDER BY seq", (seq,))
        return [{k: v for k, v in row.items() if k != "seq"} for row in rows]

    def last_seq(self) -> int:
        row = self.db.query_one("SELECT MAX(seq) AS s FROM sync_records")
        return int(row["s"] or 0) if row else 0

    # -- writing -----------------------------------------------------------
    def _store(self, row: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO sync_records (kind, key, body, hlc, pc, removed, sig, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM sync_records)) "
            "ON CONFLICT (kind, key) DO UPDATE SET body = excluded.body, hlc = excluded.hlc, "
            "pc = excluded.pc, removed = excluded.removed, sig = excluded.sig, seq = excluded.seq",
            (
                row["kind"],
                row["key"],
                row["body"],
                row["hlc"],
                row["pc"],
                row["removed"],
                row["sig"],
            ),
        )

    def put(
        self, kind: str, key: str, body: dict[str, Any], removed: bool = False
    ) -> dict[str, Any]:
        """A change made on this PC."""
        with self._lock:
            row = {
                "kind": kind,
                "key": key,
                "body": canonical(body),
                "hlc": self.clock.now(),
                "pc": self.pc,
                "removed": 1 if removed else 0,
            }
            row["sig"] = self._signature(self.keys["signing"], row)
            self._store(row)
        self._changed()
        return self._public(row)

    def remove(self, kind: str, key: str, body: dict[str, Any] | None = None) -> None:
        old = self.get(kind, key, removed=True)
        if old and old["removed"]:
            return
        self.put(kind, key, body if body is not None else (old["body"] if old else {}), True)

    def drop(self, kind: str, key: str) -> None:
        """Forget a row here only (an expired sign-in), sent nowhere."""
        self.db.execute("DELETE FROM sync_records WHERE kind = ? AND key = ?", (kind, key))

    def apply(self, row: dict[str, Any]) -> str:
        """A row from another PC: "taken", "older" (this PC has newer) or
        "refused" (bad signature or shape). Newest wins, and a removed
        moderator, sign-in or PC stays removed."""
        try:
            row = {
                "kind": str(row["kind"]),
                "key": str(row["key"]),
                "body": str(row["body"]),
                "hlc": str(row["hlc"]),
                "pc": str(row["pc"]),
                "removed": 1 if row["removed"] else 0,
                "sig": str(row["sig"]),
            }
            Clock.parse(row["hlc"])
            json.loads(row["body"])
        except (KeyError, TypeError, ValueError):
            return "refused"
        if row["kind"] not in KINDS or not self._ok(row):
            self._tell(row["kind"], row["key"])
            return "refused"
        if row["kind"] == "grants" and not row["key"].startswith(row["pc"] + ":"):
            return "refused"  # a server's permissions are only changed by its own PC
        if row["kind"] == "bot_news" and row["key"] != row["pc"]:
            return "refused"  # what a PC says about its own servers comes from that PC
        with self._lock:
            old = self.raw(row["kind"], row["key"])
            if old is not None:
                if not self._ok(old):
                    old = None  # a bad row here is replaced by a good one
                elif old["removed"] and row["kind"] in STICKY and not row["removed"]:
                    return "older"
                elif old["hlc"] >= row["hlc"] and not (
                    row["removed"] and not old["removed"] and row["kind"] in STICKY
                ):
                    return "older"
            self.clock.observe(row["hlc"])
            self._store(row)
            handler = self.applied.get(row["kind"])
            if handler is not None:
                handler(self._public(row), self._public(old) if old else None)
        self._changed()
        return "taken"

    def _changed(self) -> None:
        for hook in list(self.changed):
            try:
                hook()
            except Exception:  # pragma: no cover - a hook never stops a save
                log.exception("records hook failed")
