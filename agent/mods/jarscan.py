"""Jar check: what is known about every jar in the server's mods or plugins
folder.

Each jar is looked up by its hashes on Modrinth and CurseForge, compared
with the safety notes' list of known bad files, and opened (never run) to
look for an old log4j-core inside it, and handed to Windows antivirus
(agent/security/antivirus.py) when the PC has it. Results are kept in the database
and reused while a jar's size, modified time and SHA-256 stay the same.

A jar with a finding is moved to the quarantine folder (never deleted)
and the owner can put it back with Undo. While the server runs, players
are told in chat and the server stops after a minute so the jar can be
moved; with "Ask me first" on, it keeps running and the jar waits until
the server next stops or starts.

Before each start the owner can have the changed jars checked (and a deep
scan run) and the start waits for it, with a Skip button.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import logging
import re
import shutil
import time
import zipfile
import zlib
from pathlib import Path
from typing import Any

from .. import notes
from ..events import Event
from ..security import antivirus
from ..security.paths import assert_not_symlink, is_inside
from .curseforge import fingerprint
from .jarinfo import DISABLED_SUFFIX, NESTED_MAX_BYTES, read_mod_jar
from .manager import ModError
from .sources import SourceError

log = logging.getLogger(__name__)

STORE = "jar_scan"
DAY = 86400
WEEK = 7 * DAY
RETRY_AFTER = 3600  # a site that couldn't answer is asked again after an hour
NOTES = "Command Block safety notes"
LOG4J = "log4j-core check"
POM = "META-INF/maven/org.apache.logging.log4j/log4j-core/pom.properties"
JNDI = "org/apache/logging/log4j/core/lookup/JndiLookup.class"
NESTED_NAME = re.compile(r"(?:^|/)log4j-core-(\d[\w.\-]*)\.jar$")
MAX_DEPTH = 3
STOP_AFTER = 60  # seconds between the chat warning and the stop
STOP_WARNING = (
    "say [Command Block] A security check found a bad file on this server. "
    "The server stops in 1 minute."
)
# What reading a broken or unusual zip entry can raise.
UNREADABLE = (
    zipfile.BadZipFile,
    NotImplementedError,
    RuntimeError,
    zlib.error,
    EOFError,
    ValueError,
)


# ---------------------------------------------------------------- log4j
def _version(text: str) -> tuple[int, ...] | None:
    numbers = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", text.strip())
    return tuple(int(n or 0) for n in numbers.groups()) if numbers else None


def log4j_is_old(version: str) -> bool | None:
    """Whether a log4j-core version is one with the 2021 JNDI problems
    (Log4Shell and the ones after it). None when it can't be read."""
    v = _version(version)
    if v is None:
        return None
    if v[0] != 2:
        return False  # 1.x is another library; 3.x came after the fix
    if v[:2] == (2, 12):
        return v < (2, 12, 3)
    if v[:2] == (2, 3):
        return v < (2, 3, 1)
    return v < (2, 17, 0)


def _pom_version(jar: zipfile.ZipFile) -> str | None:
    try:
        text = jar.read(POM).decode("utf-8", "replace")
    except (KeyError, *UNREADABLE):
        return None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "version" and value.strip():
            return value.strip()
    return None


def find_log4j(data: bytes, where: str = "", depth: int = 0) -> list[dict[str, str | None]]:
    """Every log4j-core in a jar and the jars inside it: where it is and
    its version (None when it carries no version). A jar inside that
    can't be read is listed with ``unreadable`` set."""
    try:
        jar = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        return []
    found: list[dict[str, str | None]] = []
    with jar:
        names = set(jar.namelist())
        version = _pom_version(jar)
        if version or JNDI in names:
            found.append({"where": where, "version": version})
        if depth >= MAX_DEPTH:
            return found
        for entry in jar.infolist():
            if not entry.filename.endswith(".jar") or entry.file_size > NESTED_MAX_BYTES:
                continue
            try:
                data = jar.read(entry)
            except UNREADABLE:
                found.append({"where": entry.filename, "version": None, "unreadable": "yes"})
                continue
            inner = find_log4j(data, entry.filename, depth + 1)
            named = NESTED_NAME.search(entry.filename)
            for hit in inner:
                if hit["version"] is None and named and hit["where"] == entry.filename:
                    hit["version"] = named.group(1)  # the version its own file name gives
            found += inner
    return found


# ---------------------------------------------------------------- one jar
def bad_file_findings(sha512: str) -> list[dict[str, Any]]:
    """The safety notes' finding for a jar on their bad-file list, if any."""
    bad = notes.BAD_FILES.get(sha512)
    if not bad:
        return []
    return [
        {
            "scanner": NOTES,
            "words": "jarscan.finding.bad_file",
            "values": {"name": bad.get("name", ""), "why": bad.get("why", "")},
        }
    ]


def antivirus_findings(data: bytes, name: str) -> list[dict[str, Any]] | None:
    """Windows antivirus's finding for a jar, if any. None: not checked."""
    found = antivirus.scan_jar(data, name)
    if found is None:
        return None
    if not found:
        return []
    return [
        {
            "scanner": antivirus.NAME,
            "words": "jarscan.finding.antivirus",
            "values": {"where": antivirus.where_words(found, name)},
        }
    ]


def check_jar(path: Path) -> dict[str, Any]:
    """Hashes, local findings and notes for one jar. Reads it; runs nothing."""
    data = path.read_bytes()
    mod = read_mod_jar(path)
    sha512 = hashlib.sha512(data).hexdigest()
    findings = bad_file_findings(sha512)
    av = antivirus_findings(data, path.name)
    findings += av or []
    jar_notes: list[dict[str, Any]] = []
    for hit in find_log4j(data):
        where = hit["where"] or path.name
        if hit.get("unreadable"):
            jar_notes.append({"words": "jarscan.note.unreadable", "values": {"where": where}})
        elif hit["version"] is None:
            jar_notes.append({"words": "jarscan.note.log4j_no_version", "values": {"where": where}})
        elif log4j_is_old(hit["version"]):
            findings.append(
                {
                    "scanner": LOG4J,
                    "words": "jarscan.finding.log4j",
                    "values": {"version": hit["version"], "where": where},
                }
            )
    return {
        "filename": path.name,
        "name": mod.name,
        "sha256": mod.sha256,
        "sha512": sha512,
        "sha1": hashlib.sha1(data).hexdigest(),
        "fingerprint": fingerprint(data),
        "findings": findings,
        "notes": jar_notes,
        "antivirus": av is not None,
        "antivirus_tried": time.time() if av is None and antivirus.is_on() else None,
    }


def _signature(jars: dict[str, dict[str, Any]]) -> list[list[Any]]:
    return [[name, e["size"], e["modified"]] for name, e in sorted(jars.items())]


def _sites(entry: dict[str, Any], state: str) -> str:
    names = {"modrinth": "Modrinth", "curseforge": "CurseForge"}
    hits = [names[s] for s, v in (entry.get("sites") or {}).items() if v == state]
    return " and ".join(hits) if state == "found" else " or ".join(hits)


def row_words(entry: dict[str, Any], kept: bool) -> tuple[str, dict[str, Any]]:
    """The strings key and values that say one jar's result."""
    sites = entry.get("sites") or {}
    if entry["findings"]:
        if kept:
            return "jarscan.row.kept", {}
        return ("jarscan.row.waiting" if entry.get("waiting") else "jarscan.row.found"), {}
    if not sites:
        return "jarscan.row.not_yet", {}
    if "found" in sites.values():
        return "jarscan.row.known", {"sites": _sites(entry, "found")}
    if "error" in sites.values():
        return "jarscan.row.couldnt", {"sites": _sites(entry, "error")}
    return "jarscan.row.unknown", {"sites": _sites(entry, "not_found")}


# ---------------------------------------------------------------- scanner
class JarScanner:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lock = asyncio.Lock()
        self._stop_task: asyncio.Task | None = None
        self._stop_run: float | None = None
        self._skip: asyncio.Event | None = None

    @property
    def mods(self):
        return self.ctx.mods

    @property
    def quarantine_dir(self) -> Path:
        path = self.ctx.config.mod_quarantine_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def store(self) -> dict[str, Any]:
        saved = self.ctx.db.get_setting(STORE) or {}
        saved.setdefault("jars", {})
        saved.setdefault("quarantine", [])
        saved.setdefault("kept", [])
        return saved

    def save(self, saved: dict[str, Any]) -> None:
        self.ctx.db.set_setting(STORE, saved)

    def files(self) -> list[Path]:
        """The jars ModManager.scan reads, without opening them."""
        if not self.mods.server_type.has_content or not self.ctx.config.server_dir_configured:
            return []
        return [
            entry
            for entry in sorted(self.mods.mods_dir.iterdir(), key=lambda p: p.name)
            if entry.is_file()
            and not entry.is_symlink()
            and (entry.name.endswith(".jar") or entry.name.endswith(".jar" + DISABLED_SUFFIX))
        ]

    def signature(self) -> list[list[Any]]:
        """Name, size and modified time of every jar: cheap to read, and it
        changes when any jar is added, removed or replaced."""
        out = []
        for path in self.files():
            st = path.stat()
            out.append([path.name, st.st_size, st.st_mtime])
        return out

    # ------------------------------------------------------------ reading
    def _refresh(
        self, saved: dict[str, Any], antivirus_again: bool = False, catch_up: bool = True
    ) -> list[dict[str, Any]]:
        """Bring the stored entries in line with the folder. Returns the
        entries that are new, whose SHA-256 changed, or with a new finding.
        ``antivirus_again``: hand every jar to Windows antivirus again (its
        signatures update often). ``catch_up``: also hand over unchanged jars
        it hasn't checked yet (off for a server start, so the start isn't
        held up). Runs in a thread."""
        old = saved["jars"]
        jars: dict[str, dict[str, Any]] = {}
        fresh = []
        for path in self.files():
            st = path.stat()
            entry = old.get(path.name)
            if entry and entry["size"] == st.st_size and entry["modified"] == st.st_mtime:
                jars[path.name] = entry
                new = self._notes_changed(entry)
                if antivirus_again or (catch_up and self._antivirus_owed(entry)):
                    new = self._antivirus_again(entry, path) or new
                if new:
                    fresh.append(entry)
                continue
            try:
                checked = check_jar(path)
            except Exception as exc:  # one broken jar must not stop the others
                log.warning("jar check could not read %s: %s", path.name, exc)
                continue
            if entry and entry["sha256"] == checked["sha256"]:
                entry.update(size=st.st_size, modified=st.st_mtime)
                jars[path.name] = entry
                if self._notes_changed(entry):
                    fresh.append(entry)
                continue
            checked.update(size=st.st_size, modified=st.st_mtime, checked_at=time.time())
            checked.update(sites={}, looked_at=None, waiting=False)
            jars[path.name] = checked
            fresh.append(checked)
        saved["jars"] = jars
        saved["signature"] = _signature(jars)
        return fresh

    @staticmethod
    def _antivirus_owed(entry: dict[str, Any]) -> bool:
        """Not checked by Windows antivirus yet, and not tried in the last day."""
        return (
            not entry.get("antivirus") and time.time() - (entry.get("antivirus_tried") or 0) > DAY
        )

    @staticmethod
    def _antivirus_again(entry: dict[str, Any], path: Path) -> bool:
        """Check a kept entry with Windows antivirus again. True when it
        found something it hadn't before."""
        if not antivirus.is_on():
            return False
        try:
            now = antivirus_findings(path.read_bytes(), path.name)
        except OSError as exc:
            log.warning("jar check could not read %s: %s", path.name, exc)
            return False
        if now is None:
            entry["antivirus_tried"] = time.time()
            return False
        before = [f for f in entry["findings"] if f["scanner"] == antivirus.NAME]
        entry["antivirus"] = True
        entry["findings"] = [f for f in entry["findings"] if f["scanner"] != antivirus.NAME] + now
        if not entry["findings"]:
            entry["waiting"] = False
        return bool(now) and not before

    @staticmethod
    def _notes_changed(entry: dict[str, Any]) -> bool:
        """Match a kept entry's safety-notes finding to the bad-file list as
        it is now (the list is updated while the agent runs). True when a
        finding is new."""
        before = [f for f in entry["findings"] if f["scanner"] == NOTES]
        now = bad_file_findings(entry["sha512"])
        entry["findings"] = now + [f for f in entry["findings"] if f["scanner"] != NOTES]
        return bool(now) and not before

    # ------------------------------------------------------------ lookup
    def _wants_lookup(self, entry: dict[str, Any], everything: bool, now: float) -> bool:
        if everything or not entry.get("sites"):
            return True
        return "error" in entry["sites"].values() and now - (entry["looked_at"] or 0) > RETRY_AFTER

    async def _lookup(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        gate = asyncio.Semaphore(4)

        async def modrinth(entry: dict[str, Any]) -> None:
            async with gate:
                try:
                    found = await self.mods.modrinth.by_hash(entry["sha512"])
                except SourceError:
                    entry["sites"]["modrinth"] = "error"
                    return
            entry["sites"]["modrinth"] = "found" if found else "not_found"

        for entry in entries:
            entry["sites"] = {}
            entry["looked_at"] = time.time()
        await asyncio.gather(*(modrinth(e) for e in entries))
        if not self.mods.curseforge.loaders:
            return  # CurseForge has nothing for this server type
        for start in range(0, len(entries), 500):
            chunk = entries[start : start + 500]
            try:
                found = await self.mods.curseforge.by_fingerprint([e["fingerprint"] for e in chunk])
            except SourceError:
                for entry in chunk:
                    entry["sites"]["curseforge"] = "error"
                continue
            for entry in chunk:
                version = found.get(entry["fingerprint"])
                same = bool(version) and version["file"].get("sha1") == entry["sha1"]
                entry["sites"]["curseforge"] = "found" if same else "not_found"

    # ------------------------------------------------------------ scan
    async def scan(self, lookup: bool = True, everything: bool = False) -> dict[str, Any]:
        """Check the folder. ``lookup`` asks Modrinth and CurseForge about
        jars not asked yet (``everything``: about all of them again)."""
        now = time.time()
        async with self.lock:
            saved = self.store()
            fresh = await asyncio.to_thread(self._refresh, saved, everything)
            new_findings = self._mark_waiting(saved, fresh)
            self.save(saved)
            wanted = [
                dict(e)
                for e in saved["jars"].values()
                if lookup and self._wants_lookup(e, everything, now)
            ]
        for entry in new_findings:
            await self._tell(entry)
        if new_findings:
            await self.found_while_running()
        # Asked without the lock held, so a server start never waits on the internet.
        await self._lookup(wanted)
        async with self.lock:
            saved = self.store()
            for asked in wanted:
                entry = saved["jars"].get(asked["filename"])
                if entry and entry["sha256"] == asked["sha256"]:
                    entry.update(sites=asked["sites"], looked_at=asked["looked_at"])
            if lookup:
                saved["looked_up_at"] = now if everything else saved.get("looked_up_at") or now
            saved["checked_at"] = time.time()
            self.save(saved)
            if not self._busy():
                await self._move_waiting("agent")
            report = self.report()
        return report

    @staticmethod
    def _mark_waiting(saved: dict[str, Any], fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Mark the fresh entries with a finding (and not kept by Undo) for
        quarantine. Returns them."""
        found = [e for e in fresh if e["findings"] and e["sha256"] not in saved["kept"]]
        for entry in found:
            entry["waiting"] = True
        return found

    def _busy(self) -> bool:
        ctx = self.ctx
        return bool(
            ctx.server.running
            or ctx.server.state.value == "RESTART_PENDING"
            or ctx.server.held_by
            or ctx.core.jobs.risky_job(ctx.server_id)
        )

    def what_next(self) -> str:
        """The end of a Known bad message: what happens to the file now."""
        if not self.ctx.server.running:
            return (
                "The server won't be started with it: it goes to quarantine, "
                "and you can put it back with Undo."
            )
        if self.ctx.config.mods.ask_before_stop:
            return (
                "The server keeps running because Ask me first is on. Stop it to move "
                "the file to quarantine; you can put it back with Undo."
            )
        return (
            "Players were told in chat and the server stops in 1 minute, then the file "
            "goes to quarantine. You can put it back with Undo."
        )

    async def _tell(self, entry: dict[str, Any]) -> None:
        scanners = ", ".join(sorted({f["scanner"] for f in entry["findings"]}))
        await self.ctx.bus.publish(
            Event(
                type="jar_scan_found",
                level="error",
                message=f"Jar check: {scanners} found something in {entry['filename']}. "
                + self.what_next(),
                data={"filename": entry["filename"], "findings": entry["findings"]},
            )
        )

    def _anything_waiting(self) -> bool:
        jars = self.store()["jars"].values()
        return any(e.get("waiting") for e in jars) or self.ctx.deepscan.waiting()

    async def found_while_running(self) -> None:
        """Known bad is waiting while the server runs: warn players and stop
        it in a minute, unless the owner picked Ask me first."""
        from ..minecraft.process import ServerError

        server = self.ctx.server
        if not server.running or self.ctx.config.mods.ask_before_stop:
            return
        if self._stop_task and not self._stop_task.done():
            if self._stop_run == server.started_at:
                return  # a stop is planned already for this run
            self._stop_task.cancel()  # planned for a run that has ended
        with contextlib.suppress(ServerError):
            await server.send_command(STOP_WARNING, internal=True)
        self._stop_run = server.started_at
        self._stop_task = asyncio.create_task(self._stop_soon(server.started_at))

    async def _stop_soon(self, run: float | None) -> None:
        from ..minecraft.process import ServerError
        from ..minecraft.state import ExitReason

        await asyncio.sleep(STOP_AFTER)
        server = self.ctx.server
        if (
            not server.running
            or server.started_at != run  # stopped and started again since
            or self.ctx.config.mods.ask_before_stop  # turned on during the minute
            or not self._anything_waiting()
        ):
            return
        try:
            await self.ctx.server.stop("jar check", reason=ExitReason.USER_STOP)
        except ServerError as exc:
            log.warning("jar check could not stop the server: %s", exc)
            return
        async with self.lock:
            if not self._busy():
                await self._move_waiting("agent")
                await self.ctx.deepscan.move_waiting("agent")

    async def _move_waiting(self, user: str) -> list[str]:
        """Move every jar waiting for quarantine. Lock held by the caller."""
        saved = self.store()
        moved = []
        for name, entry in list(saved["jars"].items()):
            if not entry.get("waiting"):
                continue
            path = self.mods.mods_dir / name
            if not path.is_file():  # gone since it was checked
                del saved["jars"][name]
                continue
            assert_not_symlink(path)
            if not is_inside(self.mods.mods_dir, path):
                continue
            item = await self._quarantine(path, entry["name"], entry["sha256"], entry["findings"])
            del saved["jars"][name]
            saved["quarantine"].append(item)
            self._record(item, user)
            moved.append(name)
        saved["signature"] = _signature(saved["jars"])
        self.save(saved)
        if moved:
            self.mods._cache = None
        return moved

    async def _quarantine(
        self, path: Path, name: str, sha256: str, findings: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Move one file into the quarantine folder, never over another."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.quarantine_dir / f"{stamp}-{path.name}"
        count = 1
        while target.exists():
            target = self.quarantine_dir / f"{stamp}-{count}-{path.name}"
            count += 1
        await asyncio.to_thread(shutil.move, str(path), str(target))
        return {
            "id": target.name,
            "filename": path.name,
            "name": name,
            "sha256": sha256,
            "at": time.time(),
            "findings": findings,
        }

    def _record(self, item: dict[str, Any], user: str) -> None:
        scanners = ", ".join(sorted({f["scanner"] for f in item["findings"]}))
        self.mods.record(
            "quarantine",
            user,
            mod_id=None,
            mod_name=item["name"],
            source="local",
            sha256=item["sha256"],
            result="moved",
            detail=f"{scanners}: moved to {item['id']}",
        )

    async def quarantine_file(
        self,
        path: Path,
        root: str,
        rel: str,
        sha256: str,
        findings: list[dict[str, Any]],
        user: str,
    ) -> bool:
        """Move a file the deep scan found into quarantine. ``root`` and
        ``rel`` say where Undo puts it back. Lock held by the caller."""
        if not path.is_file() or path.is_symlink():
            return False
        item = await self._quarantine(path, path.name, sha256, findings)
        item.update(root=root, path=rel)
        saved = self.store()
        saved["quarantine"].append(item)
        if path.parent == self.mods.mods_dir:
            saved["jars"].pop(path.name, None)
        saved["signature"] = _signature(saved["jars"])
        self.save(saved)
        self._record(item, user)
        self.mods._cache = None
        return True

    def checks_before_start(self) -> bool:
        """Whether a start waits for the owner's checks before start."""
        settings = self.ctx.config.mods
        return bool(
            self.mods.server_type.has_content
            and self.ctx.config.server_dir_configured
            and (settings.check_before_start or settings.deep_scan_before_start)
        )

    def skip(self) -> bool:
        """The Skip button. False when no check is running."""
        if self._skip is None or self._skip.is_set():
            return False
        self._skip.set()
        return True

    async def _checks(self) -> None:
        settings = self.ctx.config.mods
        if settings.check_before_start:
            await self.scan()
        if settings.deep_scan_before_start:
            await self.ctx.deepscan.run_now("mods", "agent")

    async def _set_checking(self, on: bool) -> None:
        self.ctx.server.checking_jars = on
        await self.ctx.bus.publish(
            Event(
                type="jar_check_before_start",
                level="info",
                message="Checking jars before the start..." if on else "Jar checks done.",
                data={"checking": on},
            )
        )

    async def _wait_for_checks(self) -> None:
        """Run the checks before start and wait, until they finish or the
        owner presses Skip. A check that fails doesn't hold the start."""
        self._skip = asyncio.Event()
        checks = asyncio.create_task(self._checks())
        skip = asyncio.create_task(self._skip.wait())
        await self._set_checking(True)
        try:
            await asyncio.wait({checks, skip}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            skip.cancel()
            skipped = not checks.done()
            if skipped:
                checks.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await checks
            self._skip = None
            await self._set_checking(False)
        say = self.ctx.server._emit_console
        if skipped:
            say("[agent] the jar checks before start were skipped")
        elif not checks.cancelled() and checks.exception() is not None:
            say(f"[agent] the jar checks before start couldn't finish: {checks.exception()}")

    async def before_start(self) -> None:
        """As the server starts: run the owner's checks before start, check
        jars that changed (no lookup, so the start isn't held up by the
        internet) and move every jar waiting for quarantine. A jar that
        can't be moved stops the start."""
        from ..minecraft.process import ServerError

        checked = self.checks_before_start()
        before = len(self.store()["quarantine"])
        if checked:
            await self._wait_for_checks()
        has_content = self.mods.server_type.has_content
        async with self.lock:
            saved = self.store()
            if has_content and self.ctx.config.mods.rescan_on_change:
                fresh = await asyncio.to_thread(self._refresh, saved, False, False)
                for entry in self._mark_waiting(saved, fresh):
                    await self._tell(entry)
                self.save(saved)
            try:
                moved = await self._move_waiting("agent") if has_content else []
                moved += await self.ctx.deepscan.move_waiting("agent")
            except OSError as exc:
                raise ServerError(
                    f"A jar the jar check found something in couldn't be moved to "
                    f"quarantine ({exc}), so the server wasn't started."
                ) from exc
        if moved:
            self.ctx.server._emit_console(f"[agent] moved to quarantine: {', '.join(moved)}")
        new = [item["filename"] for item in self.store()["quarantine"][before:]]
        if checked and new:
            raise ServerError(
                f"The jar checks before start moved {', '.join(new)} to quarantine, so "
                "the server wasn't started. Start it again to start without them, or put "
                "them back with Undo."
            )

    async def tick(self) -> None:
        """The background check: everything again once a week, the jars
        that changed as soon as they change, and a site that couldn't
        answer again after an hour. Files the deep scan found waiting for
        quarantine move once the server has stopped."""
        if self.ctx.deepscan.waiting() and not self._busy():
            async with self.lock:
                await self.ctx.deepscan.move_waiting("agent")
        if not self.mods.server_type.has_content:
            return
        settings = self.ctx.config.mods
        saved = self.store()
        now = time.time()
        if settings.rescan_weekly and now - (saved.get("looked_up_at") or 0) > WEEK:
            await self.scan(everything=True)
            return
        retry = any(
            e.get("sites") and self._wants_lookup(e, False, now) for e in saved["jars"].values()
        )
        changed = False
        if settings.rescan_on_change:
            waiting = any(e.get("waiting") for e in saved["jars"].values())
            signature = await asyncio.to_thread(self.signature)
            changed = signature != saved.get("signature") or (waiting and not self._busy())
        if retry or changed:
            await self.scan()

    # ------------------------------------------------------------ undo
    async def undo(self, quarantine_id: str, user: str) -> dict[str, Any]:
        """Put a quarantined jar back, and don't move it again."""
        self.mods.require_server_offline(f"put a {self.mods.word} back")
        async with self.lock:
            saved = self.store()
            item = next((q for q in saved["quarantine"] if q["id"] == quarantine_id), None)
            if item is None:
                raise ModError("That jar isn't in quarantine.")
            source = self.quarantine_dir / item["id"]
            home = self.mods.mods_dir
            target = home / item["filename"]
            if "root" in item:  # moved by the deep scan
                home = self.ctx.deepscan.roots().get(item["root"], Path("/nonexistent"))
                target = home / item["path"]
            if not is_inside(self.quarantine_dir, source) or not is_inside(home, target):
                raise ModError("That jar isn't in quarantine.")
            if not source.is_file():
                raise ModError("The quarantined file is gone from the quarantine folder.")
            if target.exists():
                raise ModError(f"{item['filename']} is in the folder already. Remove it first.")
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.move, str(source), str(target))
            saved["quarantine"].remove(item)
            if item["sha256"] not in saved["kept"]:
                saved["kept"].append(item["sha256"])
            self.save(saved)
            self.mods.record(
                "quarantine_undo",
                user,
                mod_name=item["name"],
                source="local",
                sha256=item["sha256"],
                detail=f"Put back from {item['id']}",
            )
            self.mods._cache = None
        await self.ctx.bus.publish(
            Event(
                type="jar_scan_undo",
                level="warn",
                message=f"{item['filename']} was put back from quarantine by {user}",
                data={"filename": item["filename"]},
            )
        )
        return {"restored": item["filename"]}

    # ------------------------------------------------------------ report
    def report(self) -> dict[str, Any]:
        saved = self.store()
        kept = set(saved["kept"])
        rows = []
        for entry in sorted(saved["jars"].values(), key=lambda e: e["filename"].lower()):
            words, values = row_words(entry, entry["sha256"] in kept)
            rows.append(
                {
                    "filename": entry["filename"],
                    "name": entry["name"],
                    "sha256": entry["sha256"],
                    "checked_at": entry.get("checked_at"),
                    "sites": entry.get("sites") or {},
                    "findings": entry["findings"],
                    "notes": entry.get("notes") or [],
                    "waiting": bool(entry.get("waiting")),
                    "words": words,
                    "values": values,
                }
            )
        settings = self.ctx.config.mods
        return {
            "checked_at": saved.get("checked_at"),
            "looked_up_at": saved.get("looked_up_at"),
            "weekly": settings.rescan_weekly,
            "on_change": settings.rescan_on_change,
            "before_start": settings.check_before_start,
            "ask_first": settings.ask_before_stop,
            "checking": self._skip is not None,
            "jars": rows,
            "quarantine": sorted(saved["quarantine"], key=lambda q: -q["at"]),
            "summary": self.summary(saved),
        }

    def summary(self, saved: dict[str, Any] | None = None) -> dict[str, Any]:
        saved = saved or self.store()
        jars = list(saved["jars"].values())
        kept = set(saved["kept"])
        return {
            "checked": sum(1 for e in jars if e.get("sites")),
            "jars": len(jars),
            "found": len(saved["quarantine"])
            + sum(1 for e in jars if e["findings"] and e["sha256"] not in kept),
            "unknown": sum(
                1 for e in jars if e.get("sites") and set(e["sites"].values()) == {"not_found"}
            ),
            "couldnt": sum(1 for e in jars if "error" in (e.get("sites") or {}).values()),
            "checked_at": saved.get("checked_at"),
        }
