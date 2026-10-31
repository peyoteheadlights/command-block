"""Authentication and rate limiting.

Design:

  * The operator signs in with a username and password. The password is never
    stored - only a scrypt hash with its own salt (older PBKDF2 hashes still
    work and are re-made). It lives in the owner's signed record in the
    database (agent/security/records.py), moved in from .env at the first
    start; the owner's other PCs share it (agent/yourpcs.py).
  * A successful sign-in returns a random 256-bit session token. Only the
    SHA-256 of that token is stored, so a stolen database yields no usable
    tokens.
  * A long-lived API token (MCSC_API_TOKEN) is supported for scripts. It is
    compared in constant time.
  * Failed attempts are counted per username and per source address, and a
    lockout applies after a configurable number.
  * Helper accounts (friends with limited access) are rows in the accounts
    table, hashed exactly like the owner's password, signed in and locked
    out the same way. Their role and servers decide what they may do
    (agent/security/permissions.py).
  * The owner's password can be reset only on the PC itself
    (python -m installer.reset_password), which rewrites .env and signs
    every session on that PC out. A running agent notices the new .env and
    uses it; on a PC that shares its accounts it waits for a confirm from
    another PC first.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("msc.auth")

# OWASP's Password Storage Cheat Sheet figure for PBKDF2-HMAC-SHA256, the
# hash used before scrypt. A hash records how it was made, so older hashes
# keep working and are re-made with scrypt the next time that person signs in
# (needs_rehash).
OWASP_PBKDF2_SHA256_ROUNDS = 600_000
PBKDF2_ROUNDS = OWASP_PBKDF2_SHA256_ROUNDS
# scrypt with one of OWASP's listed settings (N=2^15, r=8, p=3): 32 MiB and
# about 0.23 s on the 4-core test machine, so well under a second on a weak
# mini PC (docs/security.md).
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 3
TOKEN_BYTES = 32


# ----------------------------------------------------------------------
# password hashing
# ----------------------------------------------------------------------
def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=128 * n * r + 2**25, dklen=32
    )


def hash_password(password: str, salt: bytes | None = None, rounds: int | None = None) -> str:
    """A self-describing hash: scrypt$N$r$p$salt$hash, or with ``rounds``
    the older pbkdf2_sha256$rounds$salt$hash (kept for tests and old
    installs)."""
    if len(password) < 10:
        raise ValueError("The password needs at least 10 characters.")
    salt = salt or secrets.token_bytes(16)
    b64 = base64.b64encode
    if rounds:
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
        return "$".join(["pbkdf2_sha256", str(rounds), b64(salt).decode(), b64(derived).decode()])
    derived = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            b64(salt).decode(),
            b64(derived).decode(),
        ]
    )


def is_password_hash(value: str) -> bool:
    return (value or "").startswith(("pbkdf2_sha256$", "scrypt$"))


def verify_password(password: str, encoded: str) -> bool:
    try:
        parts = encoded.split("$")
        given = password.encode("utf-8")
        if parts[0] == "pbkdf2_sha256" and len(parts) == 4:
            derived = hashlib.pbkdf2_hmac(
                "sha256", given, base64.b64decode(parts[2]), int(parts[1])
            )
        elif parts[0] == "scrypt" and len(parts) == 6:
            n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
            # A hash from another PC never makes this one use huge memory.
            if n > 2**17 or r > 16 or p > 16:
                return False
            derived = _scrypt(password, base64.b64decode(parts[4]), n, r, p)
            parts = [parts[0], "", "", parts[5]]
        else:
            return False
        return hmac.compare_digest(derived, base64.b64decode(parts[3]))
    except (ValueError, TypeError, AttributeError, MemoryError):
        return False


def needs_rehash(encoded: str) -> bool:
    """True for a valid older hash: PBKDF2, or scrypt with a smaller N."""
    try:
        parts = encoded.split("$")
        if parts[0] == "pbkdf2_sha256" and len(parts) == 4:
            return int(parts[1]) > 0
        return parts[0] == "scrypt" and len(parts) == 6 and int(parts[1]) < SCRYPT_N
    except (ValueError, AttributeError):
        return False


def live_session(alias: str = "") -> str:
    """The SQL test for a sign-in still in force (one parameter, now): not
    yet expired, or a phone's, which lasts until it is signed out."""
    return f"({alias}expires_at > ? OR {alias}until_signed_out = 1)"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# The installer's "Open Command Block": signs the desktop app in as the
# owner once, from this PC only, within 10 minutes. Only the code's hash and
# expiry are kept, in the agent's data folder.
ONE_TIME_FILE = "one-time-sign-in.json"
ONE_TIME_SECONDS = 10 * 60
ONE_TIME_GONE = (
    "This sign-in from the installer has run out or was used. Sign in with your password."
)


def write_one_time_sign_in(data_dir: Path) -> str:
    code = secrets.token_urlsafe(TOKEN_BYTES)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / ONE_TIME_FILE).write_text(
        json.dumps({"hash": token_hash(code), "expires_at": time.time() + ONE_TIME_SECONDS}),
        encoding="utf-8",
    )
    return code


# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Access:
    """What a moderator may do on each server. ``grants`` holds the sets the
    owner saved for named servers (an empty set hides that server). An
    "All servers" moderator also gets ``new_server`` on every other server,
    including ones added later; anyone else sees only the named servers."""

    grants: dict[str, frozenset[str]]
    all_servers: bool
    new_server: frozenset[str]
    # PC-wide permissions, such as "Start the agent" (permissions.PC_PERMISSIONS).
    pc: frozenset[str] = frozenset()

    def on(self, server_id: str) -> frozenset[str]:
        if server_id in self.grants:
            return self.grants[server_id]
        return self.new_server if self.all_servers else frozenset()

    def anywhere(self) -> frozenset[str]:
        held: set[str] = set(self.new_server) if self.all_servers else set()
        for permissions in self.grants.values():
            held |= permissions
        return frozenset(held)


@dataclass
class Principal:
    user: str
    kind: str  # session | api_token | discord (a Discord member using the bot)
    token_hash: str | None = None
    expires_at: float | None = None
    role: str = "owner"  # owner | helper (a moderator, renamed later)
    # A moderator's permissions per server. None: the owner, who may do
    # everything everywhere.
    access: Access | None = None

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    def to_dict(self) -> dict[str, Any]:
        return {
            "user": self.user,
            "kind": self.kind,
            "expires_at": self.expires_at,
            "role": self.role,
        }


# A helper's sign-in name: short, and safe to show anywhere.
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,31}$")
# Wrong invites are kept with the failed sign-ins under this name, which no
# account can have (":" isn't allowed in a user name).
INVITE_ATTEMPT = "invite:"
MAX_HELPERS = 20
_DUMMY: list[str] = []


def _dummy_hash() -> str:
    """Verified against when a username is unknown, so a wrong name takes as
    long as a wrong password and the timing doesn't say which names exist."""
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_urlsafe(16)))
    return _DUMMY[0]


class AccountError(ValueError):
    """A helper account can't be made or changed as asked."""


class InviteError(Exception):
    """An invite that can't be used: ``reason`` is unknown, used, cancelled
    or expired, so the app can say which."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401, retry_after: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retry_after = retry_after


class RateLimiter:
    """Sliding-window limit: at most ``limit`` accepted requests per caller
    in any ``window`` seconds.

    Rejected requests are not counted, so a client that keeps retrying is
    let back in as soon as its oldest accepted request leaves the window.
    Callers with no recent requests are forgotten.
    """

    # Sweep idle callers out at most this often.
    SWEEP_INTERVAL = 60.0

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = time.time()

    def check(self, key: str) -> None:
        now = time.time()
        self._sweep(now)
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            retry = int(self.window - (now - hits[0])) + 1
            raise AuthError(
                "That is too many tries at once. Wait a moment.", status=429, retry_after=retry
            )
        hits.append(now)

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def _sweep(self, now: float) -> None:
        if now - self._last_sweep < self.SWEEP_INTERVAL:
            return
        self._last_sweep = now
        stale = [k for k, hits in self._hits.items() if not hits or now - hits[-1] >= self.window]
        for key in stale:
            del self._hits[key]


_NEVER = object()
TAMPERED = (
    "This PC's account records didn't pass their safety check, so that sign-in is refused. "
    "If you didn't change anything, reset the owner password on the PC "
    "(python -m installer.reset_password)."
)


def _has_access(grants: dict[str, Any]) -> bool:
    """Whether one PC's saved permissions for a moderator let them see
    anything there."""
    every = grants.get("servers") is None
    if every and json.loads(grants.get("new_server_grants") or "[]"):
        return True
    if json.loads(grants.get("pc_grants") or "[]"):
        return True
    return any(json.loads(grants.get("grants") or "{}").values())


class AuthManager:
    def __init__(self, config, db, bus=None):
        from .records import Records

        self.config = config
        self.db = db
        self.bus = bus
        self.api_rate = RateLimiter(
            config.security.rate_limit_requests,
            config.security.rate_limit_window,
        )
        self.login_rate = RateLimiter(10, 300.0)
        self.records = Records(db, config.data_dir)
        self.records.tampered = self._tampered
        self.records.applied.update(
            account=self._apply_account,
            session=self._apply_session,
            owner=self._apply_owner,
            owner_reset=self._apply_owner_reset,
        )
        self.loop: asyncio.AbstractEventLoop | None = None  # the agent's, once it runs
        self._env_seen: object = _NEVER
        self._adopt()

    # ------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.records.raw("owner", "owner") or self.config.api_token)

    def joined(self) -> bool:
        """Whether this PC shares its accounts with others (agent/yourpcs.py)."""
        return any(r["key"] != self.records.pc for r in self.records.all("pc"))

    def _publish(self, type_: str, message: str, level: str = "warn", **data: Any) -> None:
        if not self.bus:
            return
        from ..events import Event

        event = Event(type=type_, level=level, message=message, data=data)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Called from a worker thread: hand it to the agent's own loop.
            if self.loop is not None and not self.loop.is_closed():
                self.loop.call_soon_threadsafe(self.bus.publish_soon, event)
            return
        self.bus.publish_soon(event)

    def _tampered(self, kind: str) -> None:
        self.db.audit("records_tampered", result="refused", detail=kind)
        self._publish(
            "records_tampered",
            "An account record on this PC failed its safety check and wasn't used",
            level="error",
            kind=kind,
        )

    def _adopt(self) -> None:
        """Sign what was here before shared accounts, once: the owner from
        .env, every moderator and every sign-in still in force. After that a
        row without a good record is never used."""
        self._refresh_owner()
        if self.db.get_setting("records_adopted"):
            return
        # Brought in by "Import from another PC": a new id each.
        self.db.execute("UPDATE accounts SET id = lower(hex(randomblob(8))) WHERE id IS NULL")
        for row in self.db.query("SELECT * FROM accounts"):
            self._record_account(row)
        rows = self.db.query(f"SELECT * FROM sessions WHERE {live_session()}", (time.time(),))
        for row in rows:
            self._record_session(row)
        self.db.set_setting("records_adopted", time.time())

    # -- the owner -----------------------------------------------------------
    # The owner's name and password hash live in the signed "owner" record,
    # shared by the owner's PCs. .env keeps the first-run value only: when it
    # changes (python -m installer.reset_password, or the installer run again
    # with a new password) that counts as a reset on this PC.
    def _env_stamp(self) -> int | None:
        path = getattr(self.config, "env_path", None)
        try:
            return path.stat().st_mtime_ns if path else None
        except OSError:
            return None

    def _refresh_owner(self) -> None:
        stamp = self._env_stamp()
        if stamp == self._env_seen:
            return
        first = self._env_seen is _NEVER
        self._env_seen = stamp
        if stamp is not None and not first:
            try:
                text = self.config.env_path.read_text(encoding="utf-8")
            except OSError:
                return
            for raw in text.splitlines():
                key, _, value = raw.strip().partition("=")
                if key.strip() in ("MCSC_ADMIN_PASSWORD_HASH", "MCSC_ADMIN_USERNAME") and value:
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")
        name, hashed = self.config.admin_username, self.config.admin_password_hash
        if not is_password_hash(hashed):
            return
        seen = f"{name}\n{hashed}"
        if self.db.get_setting("owner_env_seen") == seen:
            return
        self.db.set_setting("owner_env_seen", seen)
        if self.records.raw("owner", "owner") is None:
            self.records.put(
                "owner", "owner", {"username": name, "password_hash": hashed, "changed": ""}
            )
            return
        log.info("the owner's sign-in details in .env changed; using the new ones")
        self.reset_owner(name, hashed)

    def reset_owner(self, username: str, hashed: str) -> None:
        """A new owner password set on this PC itself. On its own it is the
        owner's password at once. On a PC that shares its accounts it works
        here only, until an owner device signed in to another PC confirms it
        (Your PCs), so one PC can't take over the others."""
        body = {"username": username, "password_hash": hashed, "changed": self.records.clock.now()}
        if self.joined():
            self.records.put("owner_reset", self.records.pc, body)
            self._publish(
                "owner_password_reset",
                "The owner password was reset on this PC. It works on the other PCs "
                "once you confirm it from one of them.",
                pc=self.records.pc,
            )
        else:
            self.records.put("owner", "owner", body)
            self._publish("owner_password_changed", "The owner password was changed")
        self.db.audit("owner_password_changed", user=username)
        log.info("the owner's password changed; the owner's sign-ins have ended")

    def owner(self) -> dict[str, Any]:
        """The owner on this PC: username, password_hash and changed (when the
        password last changed). A reset made here and not confirmed yet wins
        here only."""
        self._refresh_owner()
        shared = self.records.get("owner", "owner")
        reset = self.records.get("owner_reset", self.records.pc)
        if reset and (shared is None or reset["hlc"] > shared["hlc"]):
            return reset["body"]
        if shared is None:
            if self.records.raw("owner", "owner") is not None:
                raise AuthError(TAMPERED, status=503)
            raise AuthError(
                "No password is configured. Set MCSC_ADMIN_PASSWORD_HASH in the agent's .env file "
                "(use: python -m installer.make_secrets).",
                status=503,
            )
        return shared["body"]

    def _save_owner(self, **changes: Any) -> None:
        """Change the owner record in force here (the reset, while it waits)."""
        reset = self.records.get("owner_reset", self.records.pc)
        shared = self.records.get("owner", "owner")
        if reset and (shared is None or reset["hlc"] > shared["hlc"]):
            self.records.put("owner_reset", self.records.pc, {**reset["body"], **changes})
        elif shared:
            self.records.put("owner", "owner", {**shared["body"], **changes})

    @property
    def owner_username(self) -> str:
        try:
            return str(self.owner()["username"])
        except AuthError:
            return self.config.admin_username

    def _is_owner_name(self, username: str) -> bool:
        return hmac.compare_digest(username.lower(), self.owner_username.lower())

    def _apply_owner(self, record: dict[str, Any], old: dict[str, Any] | None) -> None:
        if old and old["body"].get("password_hash") != record["body"].get("password_hash"):
            if old["body"].get("changed") != record["body"].get("changed"):
                self._publish("owner_password_changed", "The owner password was changed")

    def _apply_owner_reset(self, record: dict[str, Any], old: dict[str, Any] | None) -> None:
        if record["key"] != self.records.pc and not record["removed"]:
            self._publish(
                "owner_password_reset",
                "The owner password was reset on another PC. Open Your PCs to confirm it.",
                pc=record["key"],
            )

    def pending_resets(self) -> list[dict[str, Any]]:
        """Resets made on other PCs that wait for this PC's owner to confirm."""
        shared = self.records.get("owner", "owner")
        return [
            {"pc": r["key"], "at": r["body"]["changed"]}
            for r in self.records.all("owner_reset")
            if r["key"] != self.records.pc and (shared is None or r["hlc"] > shared["hlc"])
        ]

    def confirm_reset(self, principal: Principal, pc: str) -> None:
        """An owner device signed in to this PC, before the reset was made,
        confirms a reset made on another PC: it becomes everyone's."""
        reset = next((r for r in self.pending_resets() if r["pc"] == pc), None)
        made = self.records.get("session", principal.token_hash or "")
        if reset is None:
            raise AccountError("There's no password reset waiting from that PC.")
        if pc == self.records.pc or not made or made["body"].get("created", "") >= reset["at"]:
            raise AuthError(
                "Confirm it from a device that was signed in to another PC before the reset.",
                status=403,
            )
        body = self.records.get("owner_reset", pc)
        assert body is not None
        self.records.put("owner", "owner", body["body"])
        self.db.audit("owner_reset_confirmed", user=principal.user, target=pc)

    def _locked_out(self, user: str, source_ip: str) -> float | None:
        max_failed = self.config.security.max_failed_logins
        window = self.config.security.lockout_minutes * 60
        cutoff = time.time() - window
        rows = self.db.query(
            "SELECT ts FROM login_attempts WHERE success = 0 AND ts > ? AND (user = ? OR source_ip = ?) "
            "ORDER BY ts DESC",
            (cutoff, user, source_ip),
        )
        if len(rows) >= max_failed:
            return rows[0]["ts"] + window - time.time()
        return None

    def _invites_locked_out(self, source_ip: str) -> float | None:
        """Wrong invites lock out the address they came from only: one
        person guessing doesn't stop everyone else's invite."""
        window = self.config.security.lockout_minutes * 60
        rows = self.db.query(
            "SELECT ts FROM login_attempts WHERE success = 0 AND ts > ? AND user = ? "
            "AND source_ip = ? ORDER BY ts DESC",
            (time.time() - window, INVITE_ATTEMPT, source_ip),
        )
        if len(rows) >= self.config.security.max_failed_logins:
            return rows[0]["ts"] + window - time.time()
        return None

    def _record_attempt(self, user: str, source_ip: str, success: bool) -> None:
        self.db.insert(
            "login_attempts",
            {
                "ts": time.time(),
                "user": user,
                "source_ip": source_ip,
                "success": 1 if success else 0,
            },
        )

    def failed_login_count(self, hours: float = 24) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM login_attempts WHERE success = 0 AND ts > ?",
            (time.time() - hours * 3600,),
        )
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------
    def login(
        self,
        username: str,
        password: str,
        source_ip: str = "",
        label: str = "",
        remember: bool = False,
        until_signed_out: bool = False,
    ) -> dict[str, Any]:
        """Sign in. `until_signed_out` (the phone app's sign-in) never ends by
        time: only signing out, the account going or its password changing
        ends it."""
        username = (username or "").strip()[:64]
        self.login_rate.check(f"login:{source_ip or 'unknown'}")
        owner = self.owner()
        account = None
        if not hmac.compare_digest(username, owner["username"]):
            account = self.account(username)
            if account:
                username = account["username"]  # helpers' names ignore capitals
        remaining = self._locked_out(username, source_ip)
        if remaining and remaining > 0:
            raise AuthError(
                f"Too many failed attempts. Try again in {int(remaining / 60) + 1} minutes.",
                status=429,
                retry_after=int(remaining),
            )
        if account is not None:
            password_ok = verify_password(password or "", account["password_hash"])
        elif hmac.compare_digest(username, owner["username"]):
            password_ok = verify_password(password or "", owner["password_hash"])
        else:
            verify_password(password or "", _dummy_hash())
            password_ok = False
        if not password_ok:
            self._record_attempt(username, source_ip, False)
            self.db.audit("login_failed", user=username, source_ip=source_ip, result="denied")
            if self.bus:
                from ..events import Event

                # Counted from the same rows the lockout reads, so the alert
                # says how many tries there really were (house rule 1).
                minutes = int(self.config.security.lockout_minutes)
                tries = len(
                    self.db.query(
                        "SELECT ts FROM login_attempts WHERE success = 0 AND ts > ? "
                        "AND (user = ? OR source_ip = ?)",
                        (time.time() - minutes * 60, username, source_ip),
                    )
                )
                where = source_ip or "unknown address"
                message = f"Failed sign-in for '{username}' from {where}"
                if tries > 1:
                    message = (
                        f"{tries} failed sign-ins in {minutes} minutes for '{username}' "
                        f"or from {where}"
                    )
                self.bus.publish_soon(
                    Event(
                        type="auth_failure",
                        level="warn",
                        message=message,
                        data={"username": username, "source_ip": source_ip, "tries": tries},
                    )
                )
            raise AuthError("That username or password isn't right.")

        self._record_attempt(username, source_ip, True)
        self._upgrade_hash(username, password, account)
        return self._new_session(
            username,
            account["role"] if account else "owner",
            source_ip=source_ip,
            label=label,
            remember=remember,
            until_signed_out=until_signed_out,
        )

    def sign_in_once(self, code: str, source_ip: str, label: str = "") -> dict[str, Any]:
        """The installer's one-time code (write_one_time_sign_in). The route
        checks the request came from this PC."""
        self.login_rate.check(f"login:{source_ip or 'unknown'}")
        path = self.config.data_dir / ONE_TIME_FILE
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        given = token_hash(code or "")
        if not hmac.compare_digest(str(saved.get("hash") or ""), given):
            self.db.audit("login_failed", user="one-time code", source_ip=source_ip)
            raise AuthError(ONE_TIME_GONE)
        # Works once, used or run out. Remembered in the database too, in
        # case the installer's file can't be deleted by the agent's account.
        used = self.db.get_setting("one_time_used") == given
        self.db.set_setting("one_time_used", given)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.warning("The one-time sign-in file couldn't be deleted: %s", path)
        if used or time.time() > float(saved.get("expires_at") or 0):
            raise AuthError(ONE_TIME_GONE)
        return self._new_session(
            self.owner()["username"],
            "owner",
            source_ip=source_ip,
            label=label,
            remember=True,
            until_signed_out=True,
        )

    def _new_session(
        self,
        username: str,
        role: str,
        source_ip: str,
        label: str,
        remember: bool,
        until_signed_out: bool,
    ) -> dict[str, Any]:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        expires = time.time() + self._lifetime(remember)
        row = {
            "token_hash": token_hash(token),
            "user": username,
            "created_at": time.time(),
            "expires_at": expires,
            "last_used": time.time(),
            "source_ip": source_ip,
            "label": label[:80],
            "remember": 1 if remember or until_signed_out else 0,
            "until_signed_out": 1 if until_signed_out else 0,
        }
        self.db.insert("sessions", row)
        self._record_session(row)
        self.db.audit("login", user=username, source_ip=source_ip)
        self.purge_expired()
        return {
            "token": token,
            "user": username,
            "expires_at": None if until_signed_out else expires,
            "role": role,
            "remember": bool(remember),
        }

    # -- signed-in devices, shared by the owner's PCs ---------------------
    def _account_id(self, user: str) -> str | None:
        if self._is_owner_name(user):
            return "owner"
        row = self.db.query_one("SELECT id FROM accounts WHERE username = ?", (user,))
        return row["id"] if row else None

    def _record_session(self, row: dict[str, Any], created: str | None = None) -> None:
        """A sign-in's record: the same token then works on every PC. Only
        what never changes is shared; last use and expiry renew per PC."""
        self.records.put(
            "session",
            row["token_hash"],
            {
                "user": row["user"],
                "account": self._account_id(row["user"]),
                "created": created or self.records.clock.now(),
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "remember": int(row["remember"] or 0),
                "until_signed_out": int(row["until_signed_out"] or 0),
                "label": row.get("label") or "",
                "source_ip": row.get("source_ip") or "",
            },
        )

    def _end_sessions(self, where: str, params: tuple = ()) -> int:
        """Sign these out on every PC: each gets a "removed" marker."""
        rows = self.db.query(f"SELECT token_hash FROM sessions WHERE {where}", params)
        for row in rows:
            self.records.remove("session", row["token_hash"])
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
        return len(rows)

    def _forget_session(self, digest: str) -> None:
        """An expired sign-in, gone here only."""
        self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
        self.records.drop("session", digest)

    def _apply_session(self, record: dict[str, Any], old: dict[str, Any] | None) -> None:
        digest, body = record["key"], record["body"]
        if record["removed"]:
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
            return
        if not body.get("until_signed_out") and float(body.get("expires_at") or 0) < time.time():
            return
        user = body.get("user") or ""
        if body.get("account") == "owner":
            user = self.owner_username
        elif body.get("account"):
            found = self.db.query_one(
                "SELECT username FROM accounts WHERE id = ?", (body["account"],)
            )
            if not found:
                return
            user = found["username"]
        self.db.execute(
            "INSERT OR IGNORE INTO sessions (token_hash, user, created_at, expires_at, last_used, "
            "source_ip, label, remember, until_signed_out) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                digest,
                user,
                body.get("created_at") or time.time(),
                body.get("expires_at") or 0,
                body.get("created_at"),
                body.get("source_ip") or "",
                body.get("label") or "",
                int(body.get("remember") or 0),
                int(body.get("until_signed_out") or 0),
            ),
        )

    def _upgrade_hash(self, username: str, password: str, account: dict[str, Any] | None) -> None:
        """Re-hash a password stored the older way (PBKDF2, or scrypt with
        less work), now that the right password is in hand. It is the same
        password, so nobody is signed out. A failure only logs: the old hash
        keeps working."""
        stored = account["password_hash"] if account else self.owner()["password_hash"]
        if not needs_rehash(stored):
            return
        try:
            new_hash = hash_password(password)
        except ValueError:  # a password from before the length rule
            return
        if account is not None:
            self.db.execute(
                "UPDATE accounts SET password_hash = ? WHERE username = ?",
                (new_hash, account["username"]),
            )
            self._record_account(account["username"])
        else:
            self._save_owner(password_hash=new_hash)
        log.info("re-hashed %s's password with scrypt", username)

    def _lifetime(self, remember: bool) -> float:
        security = self.config.security
        return security.remember_days * 86400 if remember else security.session_hours * 3600

    def authenticate(self, token: str | None, source_ip: str = "") -> Principal:
        if not token:
            raise AuthError("You have to be signed in to do that.")
        api_token = self.config.api_token
        if api_token and hmac.compare_digest(token, api_token):
            return Principal(user="api-token", kind="api_token")
        digest = token_hash(token)
        row = self.db.query_one("SELECT * FROM sessions WHERE token_hash = ?", (digest,))
        if not row:
            raise AuthError("You've been signed out. Sign in again.")
        kept = bool(row.get("until_signed_out"))
        if not kept and row["expires_at"] < time.time():
            self._forget_session(digest)
            raise AuthError("Your session has expired. Sign in again.")
        record = self.records.get("session", digest)
        if record is None or record["body"].get("account") != self._account_id(row["user"]):
            if record is None and self.records.raw("session", digest) is None:
                self._tampered("session")
            raise AuthError(TAMPERED)
        role, access = self._role_of(row["user"])
        if role is None:
            # The helper account was removed: its sessions end with it.
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
            raise AuthError("You've been signed out. Sign in again.")
        if role == "owner" and record["body"].get("created", "") < self.owner()["changed"]:
            # Made before the owner password last changed: that ends it.
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
            raise AuthError("You've been signed out. Sign in again.")
        now = time.time()
        expires_at = row["expires_at"]
        if row.get("remember") or kept:
            # A kept sign-in lasts remember_days from its last use.
            expires_at = max(expires_at, now + self._lifetime(True))
        self.db.execute(
            "UPDATE sessions SET last_used = ?, expires_at = ? WHERE token_hash = ?",
            (now, expires_at, digest),
        )
        row["expires_at"] = expires_at
        return Principal(
            user=row["user"],
            kind="session",
            token_hash=digest,
            expires_at=None if kept else row["expires_at"],
            role=role,
            access=access,
        )

    def _role_of(self, user: str) -> tuple[str | None, Access | None]:
        """The role and per-server permissions of a signed-in name, or
        (None, None) when no such account exists any more."""
        if self._is_owner_name(user):
            return "owner", None
        account = self.account(user)
        if not account:
            return None, None
        return account["role"], account["access"]

    def may_see_alert(self, user: str, server_id: str | None) -> bool:
        """Whether this account's phones are told about an alert: a moderator
        only about the servers they can see now, and alerts about the PC
        itself (failed sign-ins, the certificate) only go to the owner."""
        role, access = self._role_of(user)
        if role is None:
            return False
        if server_id is None:
            return role == "owner"
        return access is None or bool(access.on(server_id))

    # -- helper accounts ---------------------------------------------------
    @staticmethod
    def _account_row(row: dict[str, Any]) -> dict[str, Any]:
        # Helpers from before per-server permissions were given theirs by
        # database migration 14.
        servers = json.loads(row["servers"]) if row.get("servers") else None
        new_server = frozenset(json.loads(row.get("new_server_grants") or "[]"))
        grants = {sid: frozenset(p) for sid, p in json.loads(row.get("grants") or "{}").items()}
        pc = frozenset(json.loads(row.get("pc_grants") or "[]"))
        access = Access(grants=grants, all_servers=servers is None, new_server=new_server, pc=pc)
        return {
            **row,
            "servers": frozenset(servers) if servers is not None else None,
            "access": access,
        }

    def _save_access(self, name: str, access: Access) -> None:
        self.db.execute(
            "UPDATE accounts SET servers = ?, grants = ?, new_server_grants = ?, pc_grants = ? "
            "WHERE username = ?",
            (
                None if access.all_servers else json.dumps(sorted(access.grants)),
                json.dumps({sid: sorted(p) for sid, p in sorted(access.grants.items())}),
                json.dumps(sorted(access.new_server)),
                json.dumps(sorted(access.pc)),
                name,
            ),
        )
        self._record_account(name)

    # -- the records behind a moderator --------------------------------------
    # One "account" record (name, password hash), shared and newest-wins, and
    # one "grants" record per PC: what they may do on that PC's servers,
    # written only by that PC.
    ACCOUNT_FIELDS = (
        "username",
        "password_hash",
        "role",
        "created_at",
        "created_by",
        "password_changed_at",
    )
    GRANT_FIELDS = ("servers", "grants", "new_server_grants", "pc_grants")

    def _record_account(self, row_or_name: dict[str, Any] | str, made: str | None = None) -> None:
        row = (
            row_or_name
            if isinstance(row_or_name, dict)
            else self.db.query_one("SELECT * FROM accounts WHERE username = ?", (row_or_name,))
        )
        if not row:
            return
        old = self.records.get("account", row["id"])
        body = {k: row.get(k) for k in self.ACCOUNT_FIELDS}
        body["made"] = (
            made or (old["body"].get("made") if old else None) or self.records.clock.now()
        )
        if not old or {k: old["body"].get(k) for k in body} != body:
            self.records.put("account", row["id"], body)
        self._record_grants(row)

    def _record_grants(self, row: dict[str, Any]) -> None:
        key = f"{self.records.pc}:{row['id']}"
        body = {k: row.get(k) for k in self.GRANT_FIELDS}
        old = self.records.get("grants", key)
        if not old or old["body"] != body:
            self.records.put("grants", key, body)

    def _signed(self, row: dict[str, Any]) -> bool:
        """The row matches its signed records (a row changed straight in the
        database file doesn't)."""
        record = self.records.get("account", row.get("id") or "")
        grants = self.records.get("grants", f"{self.records.pc}:{row.get('id')}")
        ok = (
            record is not None
            and grants is not None
            and all(record["body"].get(k) == row.get(k) for k in ("username", "password_hash"))
            and grants["body"] == {k: row.get(k) for k in self.GRANT_FIELDS}
        )
        if not ok:
            self.records._tell("account", row.get("id") or "")
        return ok

    def _apply_account(self, record: dict[str, Any], old: dict[str, Any] | None) -> None:
        """A moderator added, changed or removed on another PC."""
        rid, body = record["key"], record["body"]
        here = self.db.query_one("SELECT * FROM accounts WHERE id = ?", (rid,))
        if record["removed"]:
            if here:
                self._drop_account(here["username"])
            return
        name = str(body.get("username") or "")
        clash = self.db.query_one(
            "SELECT * FROM accounts WHERE username = ? AND id != ?", (name, rid)
        )
        if clash is not None:
            name = self._settle_clash(clash, rid, body)
        fields = {k: body.get(k) for k in self.ACCOUNT_FIELDS}
        fields["username"] = name
        if here is None:
            self.db.insert(
                "accounts",
                {
                    **fields,
                    "id": rid,
                    "servers": "[]",
                    "grants": "{}",
                    "new_server_grants": "[]",
                    "pc_grants": "[]",
                },
            )
        else:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.db.execute(f"UPDATE accounts SET {sets} WHERE id = ?", (*fields.values(), rid))
            if here["username"] != name:
                self._renamed(rid, here["username"], name)
        if name != body.get("username"):
            self._record_account(name, made=body.get("made"))
        else:
            self._record_grants(self.db.query_one("SELECT * FROM accounts WHERE id = ?", (rid,)))

    def _settle_clash(self, clash: dict[str, Any], rid: str, body: dict[str, Any]) -> str:
        """Two moderators with one name, made on two PCs apart: the older
        keeps it, the newer becomes "Sam 2", and the owner is told. Every PC
        decides the same way."""
        theirs = (str(body.get("made") or ""), rid)
        mine_record = self.records.get("account", clash["id"])
        mine = (str((mine_record or {"body": {}})["body"].get("made") or ""), clash["id"])
        name = str(body.get("username"))
        number = 2
        # A sign-in name has no spaces, so "Sam" becomes "Sam2".
        while self.db.query_one("SELECT 1 FROM accounts WHERE username = ?", (f"{name}{number}",)):
            number += 1
        new_name = f"{name[: 32 - len(str(number))]}{number}"
        self._publish(
            "moderator_renamed",
            f"Two moderators were both called {name}. The newer one is now {new_name}.",
            username=name,
            renamed=new_name,
        )
        if theirs > mine:
            return new_name
        self.db.execute("UPDATE accounts SET username = ? WHERE id = ?", (new_name, clash["id"]))
        self._renamed(clash["id"], name, new_name)
        self._record_account(new_name)
        return name

    def _renamed(self, rid: str, old: str, new: str) -> None:
        keys = self.db.query(
            "SELECT key FROM sync_records WHERE kind = 'session' "
            "AND json_extract(body, '$.account') = ?",
            (rid,),
        )
        for row in keys:
            self.db.execute("UPDATE sessions SET user = ? WHERE token_hash = ?", (new, row["key"]))
        self.db.execute("UPDATE invites SET username = ? WHERE username = ?", (new, old))

    def account(self, username: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM accounts WHERE username = ?", ((username or "")[:64],)
        )
        return self._account_row(row) if row and self._signed(row) else None

    def accounts(self) -> list[dict[str, Any]]:
        """Every helper, without the password hashes."""
        rows = self.db.query("SELECT * FROM accounts ORDER BY username COLLATE NOCASE")
        out = []
        for row in rows:
            if not self._signed(row):
                continue
            account = self._account_row(row)
            sessions = self.db.query_one(
                "SELECT COUNT(*) AS n, MAX(last_used) AS last FROM sessions "
                f"WHERE user = ? AND {live_session()}",
                (account["username"], time.time()),
            ) or {"n": 0, "last": None}
            last_login = self.db.query_one(
                "SELECT MAX(ts) AS ts FROM login_attempts WHERE user = ? AND success = 1",
                (account["username"],),
            )
            out.append(
                {
                    "username": account["username"],
                    "role": account["role"],
                    "servers": sorted(account["servers"])
                    if account["servers"] is not None
                    else None,
                    "access": account["access"],
                    # No password yet: they haven't used their invite.
                    "invite_waiting": not account["password_hash"],
                    "invite": self.open_invite(account["username"]),
                    "created_at": account["created_at"],
                    "created_by": account["created_by"],
                    "password_changed_at": account["password_changed_at"],
                    "active_sessions": int(sessions["n"] or 0),
                    "last_used": sessions["last"],
                    "last_sign_in": last_login["ts"] if last_login else None,
                }
            )
        return out

    def _check_new_password(self, password: str) -> str:
        if len(password or "") < 10:
            raise AccountError("The password needs at least 10 characters.")
        if len(password) > 256:
            raise AccountError("That password is too long.")
        return password

    def create_account(
        self,
        username: str,
        password: str | None,
        servers: list[str] | None,
        by: str,
        permissions: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """A new moderator with ``permissions`` (default: "Look after it") on
        each of ``servers``, or on every server now and later when None."""
        from .permissions import LOOK_AFTER_IT

        username = (username or "").strip()
        if not USERNAME_RE.match(username):
            raise AccountError(
                "A username is 2 to 32 letters, numbers, dots, dashes or underscores, "
                "starting with a letter or number."
            )
        if self._is_owner_name(username) or username.lower() == "api-token":
            raise AccountError("That name is already taken.")
        if self.account(username):
            raise AccountError(f"There is already a moderator called {username}.")
        count = self.db.query_one("SELECT COUNT(*) AS n FROM accounts") or {"n": 0}
        if int(count["n"]) >= MAX_HELPERS:
            raise AccountError(f"There can be at most {MAX_HELPERS} moderators.")
        # No password: the moderator chooses one with an invite.
        encoded = hash_password(self._check_new_password(password)) if password is not None else ""
        now = time.time()
        given = LOOK_AFTER_IT if permissions is None else permissions
        self.db.insert(
            "accounts",
            {
                "username": username,
                "password_hash": encoded,
                "role": "helper",
                "servers": json.dumps(sorted(servers)) if servers is not None else None,
                "grants": json.dumps({sid: sorted(given) for sid in sorted(servers or ())}),
                "new_server_grants": json.dumps(sorted(given)),
                "created_at": now,
                "created_by": by,
                "password_changed_at": now,
                "id": secrets.token_hex(8),
            },
        )
        self._record_account(username)
        self.db.audit("helper_added", user=by, target=username)
        return self.account(username) or {}

    def update_account(
        self,
        username: str,
        by: str,
        servers: list[str] | None | bool = False,
        password: str | None = None,
        grants: dict[str, frozenset[str]] | None = None,
        all_servers: bool | None = None,
        new_server: frozenset[str] | None = None,
        pc: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """Change a moderator's servers or permissions, or set a new password,
        which signs them out everywhere.

        ``grants`` sets the permissions on the servers it names and leaves
        the others as they are (an empty set hides that server).
        ``all_servers`` and ``new_server`` decide what the servers it doesn't
        name get. ``servers`` is the older form (False: leave as is): a list
        limits the moderator to those servers, keeping what they had on each
        ("Look after it" where they had nothing); None is every server."""
        from .permissions import LOOK_AFTER_IT

        account = self.account(username)
        if not account:
            raise AccountError(f"There is no moderator called {username}.")
        name = account["username"]
        access: Access = account["access"]
        before = access
        if servers is not False:
            if isinstance(servers, list):
                access = Access(
                    grants={sid: access.on(sid) or LOOK_AFTER_IT for sid in servers},
                    all_servers=False,
                    new_server=access.new_server,
                    pc=access.pc,
                )
            else:
                access = Access(
                    grants=access.grants,
                    all_servers=True,
                    new_server=access.new_server,
                    pc=access.pc,
                )
        if (
            grants is not None
            or all_servers is not None
            or new_server is not None
            or pc is not None
        ):
            access = Access(
                grants={**access.grants, **(grants or {})},
                all_servers=access.all_servers if all_servers is None else all_servers,
                new_server=access.new_server if new_server is None else new_server,
                pc=access.pc if pc is None else pc,
            )
        if access != before:
            self._save_access(name, access)
            self.db.audit(
                "helper_permissions",
                user=by,
                target=name,
                detail=json.dumps(
                    {
                        "servers": sorted(access.grants) if not access.all_servers else "all",
                        "changed": sorted((grants or {}).keys()),
                        "pc": sorted(access.pc),
                    }
                ),
            )
        if password is not None:
            encoded = hash_password(self._check_new_password(password))
            self.db.execute(
                "UPDATE accounts SET password_hash = ?, password_changed_at = ? WHERE username = ?",
                (encoded, time.time(), name),
            )
            self._record_account(name)
            self._end_sessions("user = ?", (name,))
            self.db.audit("helper_password_set", user=by, target=name)
        if self.joined() and not self.can_see_anything(name):
            # Taken off their last server on any PC: gone everywhere.
            self.delete_account(name, by)
            return {"username": name, "removed": True}
        return self.account(name) or {}

    def can_see_anything(self, name: str) -> bool:
        """Whether a moderator has any server or PC permission on any PC."""
        row = self.db.query_one("SELECT * FROM accounts WHERE username = ?", (name,))
        return bool(row) and (_has_access(row) or self.can_see_anything_elsewhere(name))

    def can_see_anything_elsewhere(self, name: str) -> bool:
        row = self.db.query_one("SELECT id FROM accounts WHERE username = ?", (name,))
        return bool(row) and any(
            _has_access(r["body"])
            for r in self.records.all("grants")
            if r["key"].endswith(f":{row['id']}") and not r["key"].startswith(self.records.pc)
        )

    # -- invites ---------------------------------------------------------
    # An invite lets a new moderator choose their password (or one who forgot
    # theirs choose a new one) and sign in. It works once, for INVITE_HOURS;
    # only its hash is stored, and a new invite ends the older ones.
    INVITE_HOURS = 24

    def invite(self, username: str, by: str, forget_password: bool = False) -> dict[str, Any]:
        """A new invite for a moderator. ``forget_password`` also clears the
        password they have and signs them out everywhere (the owner's
        "Reset password")."""
        account = self.account(username)
        if not account:
            raise AccountError(f"There is no moderator called {username}.")
        name = account["username"]
        now = time.time()
        if forget_password:
            self.db.execute("UPDATE accounts SET password_hash = '' WHERE username = ?", (name,))
            self._record_account(name)
            self._end_sessions("user = ?", (name,))
            self.db.audit("helper_password_forgotten", user=by, target=name)
        elif account["password_hash"]:
            raise AccountError(f"{name} has a password already. Reset it to send a new invite.")
        self._end_invites(name, now)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        expires = now + self.INVITE_HOURS * 3600
        self.db.insert(
            "invites",
            {
                "token_hash": token_hash(token),
                "username": name,
                "created_at": now,
                "created_by": by,
                "expires_at": expires,
            },
        )
        self.db.audit("helper_invited", user=by, target=name)
        return {"token": token, "username": name, "expires_at": expires}

    def _end_invites(self, name: str, now: float) -> int:
        cur = self.db.execute(
            "UPDATE invites SET cancelled_at = ? "
            "WHERE username = ? AND used_at IS NULL AND cancelled_at IS NULL",
            (now, name),
        )
        return cur.rowcount

    def cancel_invite(self, username: str, by: str) -> int:
        account = self.account(username)
        if not account:
            raise AccountError(f"There is no moderator called {username}.")
        ended = self._end_invites(account["username"], time.time())
        self.db.audit("helper_invite_cancelled", user=by, target=account["username"])
        return ended

    def open_invite(self, username: str) -> dict[str, Any] | None:
        """The invite a moderator hasn't used yet, if there is one."""
        return self.db.query_one(
            "SELECT created_at, expires_at FROM invites WHERE username = ? AND used_at IS NULL "
            "AND cancelled_at IS NULL AND expires_at > ? ORDER BY created_at DESC LIMIT 1",
            (username, time.time()),
        )

    def _usable_invite(self, token: str, source_ip: str) -> dict[str, Any]:
        """The invite a token belongs to, or why it can't be used. Wrong
        tokens count as failed sign-ins, so guessing is locked out."""
        self.login_rate.check(f"login:{source_ip or 'unknown'}")
        remaining = self._invites_locked_out(source_ip)
        if remaining:
            raise AuthError(
                f"Too many failed attempts. Try again in {int(remaining / 60) + 1} minutes.",
                status=429,
                retry_after=int(remaining),
            )
        row = self.db.query_one(
            "SELECT * FROM invites WHERE token_hash = ?", (token_hash(token or ""),)
        )
        if row is None:
            self._record_attempt(INVITE_ATTEMPT, source_ip, False)
            raise InviteError("unknown", "That invite isn't right. Ask the owner for a new one.")
        if row["used_at"] is not None:
            raise InviteError(
                "used", "This invite has been used already. Ask the owner for a new one."
            )
        if row["cancelled_at"] is not None:
            raise InviteError(
                "cancelled", "The owner cancelled this invite. Ask them for a new one."
            )
        if row["expires_at"] < time.time():
            raise InviteError("expired", "This invite has run out. Ask the owner for a new one.")
        if not self.account(row["username"]):
            raise InviteError(
                "cancelled", "The owner cancelled this invite. Ask them for a new one."
            )
        return row

    def check_invite(self, token: str, source_ip: str = "") -> dict[str, Any]:
        row = self._usable_invite(token, source_ip)
        return {"username": row["username"], "expires_at": row["expires_at"]}

    def accept_invite(
        self, token: str, password: str, source_ip: str = "", label: str = ""
    ) -> dict[str, Any]:
        """Set the password an invite is for and sign in, until signed out
        (only the phone app opens invites)."""
        row = self._usable_invite(token, source_ip)
        try:
            encoded = hash_password(self._check_new_password(password))
        except ValueError as exc:
            raise AccountError(str(exc)) from exc
        now = time.time()
        name = row["username"]
        cur = self.db.execute(
            "UPDATE invites SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
            (now, row["token_hash"]),
        )
        if cur.rowcount != 1:  # used by someone else a moment ago
            raise InviteError(
                "used", "This invite has been used already. Ask the owner for a new one."
            )
        self.db.execute(
            "UPDATE accounts SET password_hash = ?, password_changed_at = ? WHERE username = ?",
            (encoded, now, name),
        )
        self._record_account(name)
        self.db.audit("helper_invite_used", user=name, source_ip=source_ip, detail=label[:80])
        self._record_attempt(name, source_ip, True)
        return self._new_session(
            name, "helper", source_ip=source_ip, label=label, remember=True, until_signed_out=True
        )

    def delete_account(self, username: str, by: str) -> str:
        account = self.account(username)
        if not account:
            raise AccountError(f"There is no moderator called {username}.")
        name = account["username"]
        self._end_sessions("user = ?", (name,))
        row = self.db.query_one("SELECT id FROM accounts WHERE username = ?", (name,))
        if row:
            self.records.remove("account", row["id"])
            self.records.remove("grants", f"{self.records.pc}:{row['id']}")
        self._drop_account(name)
        self.db.audit("helper_removed", user=by, target=name)
        return name

    def _drop_account(self, name: str) -> None:
        self.db.execute("DELETE FROM accounts WHERE username = ?", (name,))
        self.db.execute("DELETE FROM sessions WHERE user = ?", (name,))
        self.db.execute("DELETE FROM invites WHERE username = ?", (name,))

    def change_own_password(self, principal: Principal, current: str, new: str) -> None:
        """A helper changing their own password. Their other sessions end;
        this one stays signed in."""
        if principal.is_owner:
            raise AccountError(
                "The owner password is changed on the server PC: run "
                "python -m installer.reset_password there."
            )
        account = self.account(principal.user)
        if not account or not verify_password(current or "", account["password_hash"]):
            raise AuthError("Your current password isn't right.", status=400)
        encoded = hash_password(self._check_new_password(new))
        self.db.execute(
            "UPDATE accounts SET password_hash = ?, password_changed_at = ? WHERE username = ?",
            (encoded, time.time(), account["username"]),
        )
        self._record_account(account["username"])
        self._end_sessions(
            "user = ? AND token_hash != ?", (account["username"], principal.token_hash or "")
        )
        self.db.audit("password_changed", user=account["username"])

    def rotate(self, principal: Principal, source_ip: str = "") -> dict[str, Any]:
        """Issue a fresh session token and invalidate the current one."""
        if principal.kind != "session" or not principal.token_hash:
            raise AuthError("Only a signed-in device can be given a new key.", status=400)
        old = (
            self.db.query_one(
                "SELECT label, remember, until_signed_out, alerts, wake_key_hash FROM sessions "
                "WHERE token_hash = ?",
                (principal.token_hash,),
            )
            or {}
        )
        remember = bool(old.get("remember"))
        kept = bool(old.get("until_signed_out"))
        token = secrets.token_urlsafe(TOKEN_BYTES)
        expires = time.time() + self._lifetime(remember)
        made = self.records.get("session", principal.token_hash)
        new_row = {
            "token_hash": token_hash(token),
            "user": principal.user,
            "created_at": time.time(),
            "expires_at": expires,
            "last_used": time.time(),
            "source_ip": source_ip,
            # The same device keeps its name on the Security page.
            "label": old.get("label") or "rotated",
            "remember": 1 if remember else 0,
            "until_signed_out": 1 if kept else 0,
            # The device's own alerts and wake key stay with it.
            "alerts": old.get("alerts"),
            "wake_key_hash": old.get("wake_key_hash"),
        }
        self.db.insert("sessions", new_row)
        # Still from before any later password change, like the old one.
        self._record_session(new_row, created=made["body"].get("created") if made else None)
        # So does its phone's push address (fcm.phones joins on the session).
        self.db.execute(
            "UPDATE app_phones SET session = ? WHERE session = ?",
            (token_hash(token), principal.token_hash),
        )
        self._end_sessions("token_hash = ?", (principal.token_hash,))
        self.db.audit("token_rotate", user=principal.user, source_ip=source_ip)
        return {"token": token, "expires_at": None if kept else expires}

    def logout(self, principal: Principal) -> None:
        if principal.token_hash:
            self._end_sessions("token_hash = ?", (principal.token_hash,))
            self.db.audit("logout", user=principal.user)

    def revoke_all(self, user: str | None = None) -> int:
        # "Sign out everywhere": every PC the owner has, too.
        ended = self._end_sessions("user = ?", (user,)) if user else self._end_sessions("1 = 1")
        self.db.audit("sessions_revoked", user=user or "all")
        return ended

    # -- preferences -----------------------------------------------------
    # How each account likes the apps (Simple or Technical, theme),
    # kept here so the choice follows the person to every device.
    PREFERENCE_CHOICES: dict[str, tuple[str, ...]] = {
        "mode": ("simple", "technical"),
        "theme": ("system", "light", "dark", "graphite", "contrast"),
    }
    PREFERENCE_DEFAULTS: dict[str, str] = {"mode": "simple", "theme": "system"}

    def preferences(self, user: str) -> dict[str, str]:
        saved = self.db.get_setting(f"user:{user}:preferences", {}) or {}
        prefs = dict(self.PREFERENCE_DEFAULTS)
        for key, choices in self.PREFERENCE_CHOICES.items():
            if saved.get(key) in choices:
                prefs[key] = saved[key]
        return prefs

    def set_preferences(self, user: str, updates: dict[str, str]) -> dict[str, str]:
        prefs = self.preferences(user)
        for key, value in updates.items():
            if value not in self.PREFERENCE_CHOICES.get(key, ()):
                raise ValueError(f"{value!r} is not a choice for {key}")
            prefs[key] = value
        self.db.set_setting(f"user:{user}:preferences", prefs)
        return prefs

    # -- alerts read -----------------------------------------------------
    # The newest alert each account has read, like a chat app's read marker:
    # every alert after it is unread, on every device the account uses.
    def alerts_read(self, user: str) -> int:
        return int(self.db.get_setting(f"user:{user}:alerts_read", 0) or 0)

    def mark_alerts_read(self, user: str, up_to: int) -> int:
        # Only moves forward, so a device that's behind can't unread anything.
        read = max(self.alerts_read(user), up_to)
        self.db.set_setting(f"user:{user}:alerts_read", read)
        return read

    def purge_expired(self) -> int:
        rows = self.db.query(
            "SELECT token_hash FROM sessions WHERE expires_at < ? AND until_signed_out = 0",
            (time.time(),),
        )
        for row in rows:
            self._forget_session(row["token_hash"])
        return len(rows)

    # -- signed-in devices ------------------------------------------------
    @staticmethod
    def session_id(digest: str) -> str:
        """A short id for one sign-in: enough to tell them apart, never
        enough to use as a token (it is part of the token's hash)."""
        return digest[:16]

    def devices(self, principal: Principal) -> list[dict[str, Any]]:
        """Signed-in devices: every account's for the owner, a helper's own."""
        rows = self.db.query(
            "SELECT token_hash, user, created_at, expires_at, last_used, source_ip, label, "
            "remember, until_signed_out FROM sessions "
            f"WHERE {live_session()} ORDER BY last_used DESC",
            (time.time(),),
        )
        out = []
        for row in rows:
            if not principal.is_owner and row["user"] != principal.user:
                continue
            digest = row.pop("token_hash")
            if row.pop("until_signed_out"):
                row["expires_at"] = None  # until it is signed out
            out.append(
                {
                    **row,
                    "id": self.session_id(digest),
                    "remember": bool(row["remember"]),
                    "current": digest == principal.token_hash,
                }
            )
        return out

    def sign_out_device(self, principal: Principal, session_id: str) -> dict[str, Any]:
        """End one sign-in. A helper can only end their own."""
        if not re.fullmatch(r"[0-9a-f]{16}", session_id or ""):
            raise AuthError("There's no such sign-in.", status=404)
        rows = self.db.query(
            "SELECT token_hash, user, label FROM sessions WHERE substr(token_hash, 1, 16) = ?",
            (session_id,),
        )
        rows = [r for r in rows if principal.is_owner or r["user"] == principal.user]
        if not rows:
            raise AuthError("There's no such sign-in.", status=404)
        for row in rows:
            self._end_sessions("token_hash = ?", (row["token_hash"],))
        return {"ok": True, "user": rows[0]["user"], "label": rows[0]["label"]}

    def active_sessions(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT user, created_at, expires_at, last_used, source_ip, label FROM sessions "
            f"WHERE {live_session()} ORDER BY last_used DESC",
            (time.time(),),
        )
        return rows
