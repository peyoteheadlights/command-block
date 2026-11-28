"""VirusTotal: an optional deep scan check with the owner's own key.

Only a file's SHA-256 is sent (GET /api/v3/files/{sha256}); files are
never uploaded. VirusTotal answers with what each antivirus engine said
about a file it has seen before, or 404 when it has never seen it.

A free key allows 4 lookups a minute and 500 a day, so lookups wait for
a free slot, the day's count is kept on disk, and an answer is kept for a
week (a day for a file VirusTotal didn't know). The key and the count
belong to the whole agent, not to one server. Each key has its own count,
so a new key isn't refused for what the old one used.

3 or more engines saying malicious makes a file Known bad; 1 or 2 make it
Worth a look. The finding always says how many of how many, and which.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import deque
from pathlib import Path
from typing import Any

from .. import downloads
from ..security.records import read_secrets, write_secrets

NAME = "VirusTotal"
API = "https://www.virustotal.com/api/v3/files/"
KEY_FILE = "virustotal-key.bin"
STATE_FILE = "virustotal.json"
PER_MINUTE = 4
PER_DAY = 500
BAD = 3  # engines saying malicious before a file is Known bad
KEEP = 7 * 86400
KEEP_UNKNOWN = 86400
MAX_NAMES = 10
MAX_ANSWER = 4 * 1024 * 1024
# The SHA-256 of an empty file: VirusTotal knows it, so it checks a new key.
EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# Replaced in tests, so a test never waits a real minute.
clock = time.monotonic
sleep = asyncio.sleep


class VirusTotalError(RuntimeError):
    """``words`` is the deep scan error the apps show."""

    def __init__(self, words: str, **values: Any) -> None:
        super().__init__(words)
        self.words = words
        self.values = values

    def said(self) -> dict[str, Any]:
        return {"words": self.words, "values": {"name": NAME, **self.values}}


class LimitReached(VirusTotalError):
    """No more lookups now: the rest of the files are left for later."""


def read(answer: dict[str, Any]) -> dict[str, Any]:
    """What VirusTotal's file answer says: how many engines called it
    malicious, of how many gave an answer, and their names."""
    try:
        return _read(answer)
    except (
        AttributeError,
        TypeError,
        ValueError,
        OverflowError,
    ) as exc:  # not the shape VirusTotal sends
        raise VirusTotalError("deepscan.error.vt_failed", why="odd answer") from exc


def _read(answer: dict[str, Any]) -> dict[str, Any]:
    attributes = (answer.get("data") or {}).get("attributes") or {}
    results = attributes.get("last_analysis_results") or {}
    stats = attributes.get("last_analysis_stats") or {}
    flagged = sorted(
        name
        for name, said in results.items()
        if isinstance(said, dict) and said.get("category") == "malicious"
    )
    total = sum(
        int(stats.get(k) or 0) for k in ("malicious", "suspicious", "undetected", "harmless")
    )
    return {
        "known": True,
        "count": len(flagged),
        "total": max(total, len(flagged)),
        "engines": flagged,
    }


def _kept_ok(seen: Any) -> bool:
    """A kept answer from the state file that can still be used as it is."""
    if not isinstance(seen, dict) or not isinstance(seen.get("at"), (int, float)):
        return False
    if not seen.get("known"):
        return True
    engines = seen.get("engines")
    return (
        isinstance(seen.get("count"), int)
        and isinstance(seen.get("total"), int)
        and isinstance(engines, list)
        and all(isinstance(name, str) for name in engines)
    )


def key_id(key: str) -> str:
    """A short name for a key, so its count can be saved without the key."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def finding(seen: dict[str, Any]) -> tuple[dict[str, Any], bool] | None:
    """The deep scan finding for one answer, and whether it is Known bad."""
    count = seen.get("count") or 0
    if not seen.get("known") or count == 0:
        return None
    engines = seen["engines"]
    names = ", ".join(engines[:MAX_NAMES])
    if len(engines) > MAX_NAMES:
        names += f" (+{len(engines) - MAX_NAMES})"
    values = {"count": count, "total": seen["total"], "engines": names}
    return {"scanner": NAME, "words": "deepscan.finding.virustotal", "values": values}, count >= BAD


class VirusTotal:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.lock = asyncio.Lock()
        self.calls: dict[str, deque[float]] = {}  # each key's last lookups

    # ------------------------------------------------------------ key
    @property
    def key_path(self) -> Path:
        return self.data_dir / KEY_FILE

    def key(self) -> str | None:
        return read_secrets(self.key_path).get("key") or None

    async def save_key(self, key: str) -> None:
        """Save the key after one lookup shows VirusTotal takes it."""
        key = key.strip()
        if not key:
            raise VirusTotalError("deepscan.error.vt_key")
        await self._ask(EMPTY, key, deadline=None, use_kept=False)
        write_secrets(self.key_path, {"key": key})

    def remove_key(self) -> None:
        self.key_path.unlink(missing_ok=True)

    # ------------------------------------------------------------ state
    @property
    def state_path(self) -> Path:
        return self.data_dir / STATE_FILE

    def state(self) -> dict[str, Any]:
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        today = time.strftime("%Y-%m-%d", time.gmtime())
        used = saved.get("used")
        if saved.get("day") != today or not isinstance(used, dict):
            saved.update(day=today, used={})  # VirusTotal's day starts at 00:00 UTC
        saved["used"] = {k: n for k, n in saved["used"].items() if isinstance(n, int)}
        seen = saved.get("seen")
        seen = seen if isinstance(seen, dict) else {}
        saved["seen"] = {sha: one for sha, one in seen.items() if _kept_ok(one)}
        return saved

    def _save(self, saved: dict[str, Any]) -> None:
        now = time.time()
        saved["seen"] = {
            sha: seen
            for sha, seen in saved["seen"].items()
            if now - seen.get("at", 0) < (KEEP if seen.get("known") else KEEP_UNKNOWN)
        }
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved), encoding="utf-8")
        tmp.replace(self.state_path)

    # ------------------------------------------------------------ lookups
    async def lookup(self, sha256: str, deadline: float | None = None) -> dict[str, Any]:
        """What VirusTotal says about one file: read() or {"known": False}.
        ``deadline`` is a clock() time after which no more lookups start."""
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise VirusTotalError("deepscan.error.vt_failed", why="not a sha256")
        key = self.key()
        if not key:
            raise VirusTotalError("deepscan.error.vt_no_key")
        return self._kept(sha256) or await self._ask(sha256, key, deadline)

    def _kept(self, sha256: str) -> dict[str, Any] | None:
        seen = self.state()["seen"].get(sha256)
        if seen and time.time() - seen["at"] < (KEEP if seen.get("known") else KEEP_UNKNOWN):
            return seen
        return None

    async def _ask(
        self, sha256: str, key: str, deadline: float | None, use_kept: bool = True
    ) -> dict[str, Any]:
        me = key_id(key)
        while True:
            await self._take_lock(deadline)
            try:  # one lookup at a time, for every server on this agent
                kept = self._kept(sha256) if use_kept else None
                if kept:  # another server asked about it while this one waited
                    return kept
                saved = self.state()
                if saved["used"].get(me, 0) >= PER_DAY:
                    raise LimitReached("deepscan.error.vt_day", limit=PER_DAY)
                calls = self.calls.setdefault(me, deque(maxlen=PER_MINUTE))
                wait = calls[0] + 60 - clock() if len(calls) == PER_MINUTE else 0
                if wait <= 0:
                    calls.append(clock())
                    saved["used"][me] = saved["used"].get(me, 0) + 1
                    self._save(saved)  # counted before the answer, as VirusTotal counts it
                    return await self._fetch(sha256, key)
            finally:
                self.lock.release()
            # Wait for a free slot without the lock, so a key check isn't held up.
            if deadline is not None and clock() + wait > deadline:
                raise LimitReached("deepscan.error.vt_time")
            await sleep(wait)

    async def _take_lock(self, deadline: float | None) -> None:
        if deadline is None:
            await self.lock.acquire()
            return
        try:
            await asyncio.wait_for(self.lock.acquire(), max(0.0, deadline - clock()))
        except TimeoutError:
            raise LimitReached("deepscan.error.vt_time") from None

    async def _fetch(self, sha256: str, key: str) -> dict[str, Any]:
        try:
            answer = await downloads.fetch_json(
                API + sha256, max_bytes=MAX_ANSWER, headers={"x-apikey": key}
            )
            seen = read(answer)
        except downloads.DownloadNotFound:
            seen = {"known": False}
        except downloads.DownloadError as exc:
            if exc.status in (401, 403):
                raise VirusTotalError("deepscan.error.vt_key") from exc
            if exc.status == 429:
                raise LimitReached("deepscan.error.vt_busy") from exc
            raise VirusTotalError("deepscan.error.vt_failed", why=str(exc)) from exc
        seen["at"] = time.time()
        saved = self.state()
        saved["seen"][sha256] = seen
        self._save(saved)
        return seen


_agents: dict[str, VirusTotal] = {}


def for_agent(data_dir: Path | str) -> VirusTotal:
    """The one VirusTotal for this agent's data folder, so every server
    shares the key's limits."""
    where = str(Path(data_dir).resolve())
    if where not in _agents:
        _agents[where] = VirusTotal(Path(where))
    return _agents[where]
