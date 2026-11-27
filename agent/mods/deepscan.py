"""Deep scan: a slower look inside files, run when someone clicks it.

The person picks the scanners and what to look at: one jar, the server's
mods or plugins folder, or the whole server folder and the Java it runs
with. Files are read and never run.

  * Command Block behaviour check: our own YARA rules
    (data/deepscan/behaviour.yar) for code that mods and plugins rarely
    have, such as starting programs or reading login files.
  * YARA Forge rules: a public rule set, downloaded the first time and
    again after a week, checked against the checksum GitHub publishes.

Both run in YARA-X, a library inside the agent. Their matches are
"Worth a look", never proof: the person decides, with a Quarantine button.

The whole-folder scope also hands every file to Windows antivirus (when it
is on) and lists programs, scripts and jars in odd places as Worth a look.
What Windows antivirus finds there is Known bad and goes to quarantine,
like the jar check's findings (agent/mods/jarscan.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import os
import shutil
import sys
import threading
import time
import zipfile
import zlib
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any

from .. import downloads
from ..events import Event
from ..security import antivirus
from ..security.paths import is_inside
from .manager import ModError

log = logging.getLogger(__name__)

STORE = "deep_scan"
RULES = Path(__file__).resolve().parent.parent.parent / "data" / "deepscan" / "behaviour.yar"
FORGE_REPO = "YARAHQ/yara-forge"
FORGE_LATEST = f"https://api.github.com/repos/{FORGE_REPO}/releases/latest"
FORGE_ASSET = "yara-forge-rules-core.zip"
FORGE_FILE = "yara-forge-core.yar"
FORGE_INFO = "yara-forge.json"
FORGE_MAX = 64 * 1024 * 1024
WEEK = 7 * 86400
MAX_FILE = 256 * 1024 * 1024  # a bigger file is skipped and counted
MAX_ENTRY = 64 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024  # stop unpacking a zip bomb
MAX_DEPTH = 3
TIMEOUT = 30  # seconds YARA-X may spend on one piece
MAX_FOUND = 500

BEHAVIOUR = "behaviour"
FORGE = "yara_forge"
SCANNERS = {
    BEHAVIOUR: {"name": "Command Block behaviour check", "speed": "quick"},
    FORGE: {"name": "YARA Forge rules", "speed": "slower"},
}
FILE_CHECK = "Command Block file check"
SCOPES = ("jar", "mods", "folder")
# Programs and scripts in the server folder are Worth a look.
PROGRAMS = {".exe", ".dll", ".bat", ".cmd", ".ps1", ".vbs", ".sh", ".scr", ".com", ".msi"}
# Forge and NeoForge's installer writes these two.
KNOWN_SCRIPTS = {"run.bat", "run.sh"}
# Jars in these top folders belong to the server or its loader.
JAR_HOMES = {"mods", "plugins", "libraries", "versions", ".fabric", ".quilt", "cache", "bundler"}
# World region files: only Windows antivirus reads them.
REGIONS = {".mca", ".mcr", ".mcc"}
UNREADABLE = (
    zipfile.BadZipFile,
    NotImplementedError,
    RuntimeError,
    zlib.error,
    EOFError,
    ValueError,
)

_forge_lock = asyncio.Lock()


class DeepScanError(ModError):
    """Written for people."""


class BrokenRules(ValueError):
    """A rules file YARA-X can't read."""


# ---------------------------------------------------------------- rules
def compile_rules(forge: Path | None) -> Any:
    """Our rules, and YARA Forge's when given. A Forge rule YARA-X can't
    read is left out."""
    import yara_x

    compiler = yara_x.Compiler()
    compiler.ignore_invalid_rules(True)
    compiler.new_namespace(BEHAVIOUR)
    compiler.add_source(RULES.read_text(encoding="utf-8"))
    if forge is not None:
        compiler.new_namespace(FORGE)
        compiler.add_source(forge.read_text(encoding="utf-8", errors="replace"))
        # A broken file is a syntax error: YARA-X then keeps none of its rules.
        if any(e.get("type") == "SyntaxError" for e in compiler.errors()):
            raise BrokenRules("the YARA Forge rules file is broken")
    return compiler.build()


def _meta(match: Any) -> dict[str, Any]:
    return {str(k): v for k, v in match.metadata}


def finding(match: Any, where: str) -> dict[str, Any]:
    """A YARA match as a finding the apps can say."""
    meta = _meta(match)
    if match.namespace == BEHAVIOUR:
        return {
            "scanner": SCANNERS[BEHAVIOUR]["name"],
            "words": str(meta.get("words", "deepscan.rule.other")),
            "values": {"rule": match.identifier, "where": where},
        }
    return {
        "scanner": SCANNERS[FORGE]["name"],
        "words": "deepscan.finding.forge",
        "values": {
            "rule": match.identifier,
            "where": where,
            "description": str(meta.get("description", ""))[:300],
            "author": str(meta.get("author", ""))[:120],
            "license": str(meta.get("license_url", ""))[:300],
        },
    }


def pieces(data: bytes, prefix: str = "", depth: int = 0, budget: list[int] | None = None):
    """The file inside a zip (and zips inside it, a few levels deep), as
    (where, bytes). A broken entry is passed over."""
    budget = budget if budget is not None else [MAX_TOTAL]
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        return
    with archive:
        for entry in archive.infolist():
            if entry.is_dir() or entry.file_size > MAX_ENTRY:
                continue
            if entry.file_size > budget[0]:
                return
            try:
                inner = archive.read(entry)
            except UNREADABLE:
                continue
            budget[0] -= len(inner)
            where = prefix + entry.filename
            yield where, inner
            if depth < MAX_DEPTH and inner.startswith(b"PK"):
                yield from pieces(inner, where + "!/", depth + 1, budget)


def yara_findings(scanner: Any, data: bytes) -> tuple[list[dict[str, Any]], bool]:
    """Every rule that matches the file or a piece inside it. Also says
    whether some piece took too long and wasn't fully checked."""
    import yara_x

    found: dict[tuple[str, str], dict[str, Any]] = {}
    slow = False
    for where, piece in [("", data), *pieces(data)]:
        try:
            matches = scanner.scan(piece).matching_rules
        except yara_x.TimeoutError:
            slow = True
            continue
        for match in matches:
            key = (match.namespace, match.identifier)
            found.setdefault(key, finding(match, where))
    return list(found.values()), slow


# ---------------------------------------------------------------- files
def file_check(rel: PurePosixPath, server_jar: str) -> dict[str, Any] | None:
    """Worth a look for a program, script or stray jar in the server
    folder. ``rel`` is the path inside the server folder."""
    suffix = rel.suffix.lower()
    top = rel.parts[0].lower() if len(rel.parts) > 1 else ""
    if rel.as_posix() == server_jar:
        return None
    if suffix in PROGRAMS:
        if len(rel.parts) == 1 and rel.name.lower() in KNOWN_SCRIPTS:
            return None
        words = "deepscan.finding.program"
    elif suffix == ".jar" and top not in JAR_HOMES and not top.startswith("plugins"):
        words = "deepscan.finding.stray_jar"
    else:
        return None
    return {"scanner": FILE_CHECK, "words": words, "values": {"where": rel.as_posix()}}


def java_root(java: str) -> Path | None:
    """The Java folder the server runs with: the folder above bin/ that
    holds Java's ``release`` file. None when it can't be told."""
    found = shutil.which(java) if java else None
    if not found:
        return None
    exe = Path(found).resolve()
    root = exe.parent.parent
    if exe.parent.name.lower() != "bin" or not (root / "release").is_file():
        return None
    return root


def walk(root: Path, skip: list[Path]) -> Iterator[Path]:
    """Every file under ``root``, sorted, without following links and
    without the folders in ``skip``."""
    for folder, dirs, files in os.walk(root, followlinks=False):
        here = Path(folder)
        dirs[:] = sorted(
            d
            for d in dirs
            if not (here / d).is_symlink() and not any(_same(here / d, s) for s in skip)
        )
        for name in sorted(files):
            path = here / name
            if not path.is_symlink():
                yield path


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@contextlib.contextmanager
def low_priority() -> Iterator[None]:
    """Run this thread below normal priority on Windows, so the server and
    the PC come first."""
    if sys.platform != "win32":
        yield
        return
    import ctypes

    kernel = ctypes.windll.kernel32
    thread = kernel.GetCurrentThread()
    before = kernel.GetThreadPriority(thread)
    kernel.SetThreadPriority(thread, -1)  # THREAD_PRIORITY_BELOW_NORMAL
    try:
        yield
    finally:
        kernel.SetThreadPriority(thread, before)


# ---------------------------------------------------------------- scanner
class DeepScanner:
    def __init__(self, ctx):
        self.ctx = ctx
        self.job_id: str | None = None

    def store(self) -> dict[str, Any]:
        saved = self.ctx.db.get_setting(STORE) or {}
        saved.setdefault("took", {})
        return saved

    def save(self, saved: dict[str, Any]) -> None:
        self.ctx.db.set_setting(STORE, saved)

    @property
    def forge_dir(self) -> Path:
        return Path(self.ctx.config.data_dir) / "deepscan"

    def forge_info(self) -> dict[str, Any] | None:
        try:
            info = json.loads((self.forge_dir / FORGE_INFO).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(info, dict) or not isinstance(info.get("at"), (int, float)):
            return None
        return info if (self.forge_dir / FORGE_FILE).is_file() else None

    def roots(self) -> dict[str, Path]:
        """Where a found file's path starts."""
        server = Path(self.ctx.config.server_dir)
        found = {"server": server}
        mods = self.ctx.config.mods_dir
        if self.ctx.mods.server_type.has_content and not is_inside(server, mods):
            found["mods"] = mods
        java = java_root(self.ctx.config.server.java)
        if java is not None:
            found["java"] = java
        return found

    # ------------------------------------------------------------ forge
    async def get_forge(self) -> dict[str, Any]:
        """YARA Forge's rules, downloaded when missing or a week old. An
        old copy is used when the download fails; with none it raises."""
        async with _forge_lock:
            info = self.forge_info()
            if info and time.time() - info["at"] < WEEK:
                return info
            try:
                return await self._download_forge()
            except (downloads.DownloadError, OSError, zipfile.BadZipFile, KeyError) as exc:
                if info:
                    log.warning("YARA Forge rules not refreshed: %s", exc)
                    return {**info, "error": str(exc)}
                raise DeepScanError(f"YARA Forge rules couldn't be downloaded: {exc}") from exc

    async def _download_forge(self) -> dict[str, Any]:
        with downloads.allow_releases(FORGE_REPO):
            release = await downloads.fetch_json(FORGE_LATEST)
            asset = next(
                (a for a in release.get("assets") or [] if a.get("name") == FORGE_ASSET), None
            )
            if asset is None:
                raise downloads.DownloadError(f"the release has no {FORGE_ASSET}")
            digest = str(asset.get("digest") or "")
            if not digest.startswith("sha256:"):
                raise downloads.DownloadError("GitHub gave no checksum for it, so it wasn't used")
            temp = self.forge_dir / "download"
            fetched = await downloads.download(
                downloads.FileSpec(
                    url=str(asset["browser_download_url"]),
                    name=FORGE_ASSET,
                    sha256=digest.removeprefix("sha256:"),
                    allow_unverified=False,
                    max_bytes=FORGE_MAX,
                ),
                temp,
            )
        info = {
            "tag": str(release.get("tag_name") or ""),
            "sha256": fetched.sha256,
            "at": time.time(),
        }
        await asyncio.to_thread(self._unpack_forge, fetched.path, info)
        shutil.rmtree(temp, ignore_errors=True)
        return info

    def _unpack_forge(self, archive: Path, info: dict[str, Any]) -> None:
        with zipfile.ZipFile(archive) as rules:
            entry = next((e for e in rules.infolist() if e.filename.endswith(".yar")), None)
            if entry is None:
                raise zipfile.BadZipFile("the download has no rules file")
            if entry.file_size > FORGE_MAX:
                raise zipfile.BadZipFile("the rules file is too big")
            data = rules.read(entry)
        part = self.forge_dir / (FORGE_FILE + ".part")
        part.write_bytes(data)
        os.replace(part, self.forge_dir / FORGE_FILE)
        (self.forge_dir / FORGE_INFO).write_text(json.dumps(info), encoding="utf-8")

    # ------------------------------------------------------------ targets
    def targets(self, scope: str, filename: str | None) -> list[tuple[str, Path, str]]:
        """(root, path, path inside root) for every file in the scope."""
        jars = self.ctx.jarscan.files()
        server = Path(self.ctx.config.server_dir)
        if scope == "jar":
            picked = [p for p in jars if p.name == filename]
            if not picked:
                raise DeepScanError("That jar isn't in the folder.")
            jars = picked
        roots = self.roots()
        home = "mods" if "mods" in roots else "server"
        if scope in ("jar", "mods"):
            return [(home, p, p.relative_to(roots[home]).as_posix()) for p in jars]
        config = self.ctx.config
        skip = [config.backup_dir, config.mod_quarantine_dir, config.mod_trash_dir]
        skip.append(Path(config.data_dir))
        out = [("server", p, p.relative_to(server).as_posix()) for p in walk(server, skip)]
        for name in ("mods", "java"):
            other = roots.get(name)
            if other is not None and not is_inside(server, other):
                out += [(name, p, p.relative_to(other).as_posix()) for p in walk(other, skip)]
        return out

    # ------------------------------------------------------------ run
    def status(self) -> dict[str, Any]:
        saved = self.store()
        job = None
        if self.job_id:
            with contextlib.suppress(Exception):
                job = self.ctx.core.jobs.get(self.job_id).to_dict()
        forge = self.forge_info()
        return {
            "scanners": [
                {
                    "id": key,
                    "name": value["name"],
                    "words": f"deepscan.scanner.{key}",
                    "what": f"deepscan.what.{key}",
                    "speed": f"deepscan.speed.{value['speed']}",
                    "took": saved["took"].get(key),
                    "downloaded": forge if key == FORGE else None,
                }
                for key, value in SCANNERS.items()
            ],
            "antivirus": antivirus.is_on(),
            "running": job if job and job["state"] == "running" else None,
            "last": saved.get("last"),
        }

    def start(self, scope: str, scanners: list[str], filename: str | None, user: str) -> dict:
        """Start a deep scan as a job. One at a time on a server."""
        if scope not in SCOPES:
            raise DeepScanError("Pick what to scan.")
        picked = [s for s in SCANNERS if s in scanners]
        if not picked:
            raise DeepScanError("Pick at least one scan to run.")
        if not self.ctx.mods.server_type.has_content and scope != "folder":
            raise DeepScanError("This server type has no mods or plugins folder.")
        if not self.ctx.config.server_dir_configured:
            raise DeepScanError("This server has no folder yet.")
        if self.job_id and self.ctx.core.jobs.get(self.job_id).state == "running":
            raise DeepScanError("A deep scan is running on this server already.")
        if scope == "jar":
            self.targets(scope, filename)  # says now when the jar isn't there

        async def run(handle) -> dict[str, Any]:
            return await self._run(handle, scope, picked, filename, user)

        job = self.ctx.core.jobs.start(
            "deep_scan", "Deep scan", run, server_id=self.ctx.server_id, user=user
        )
        self.job_id = job.id
        return self.status()

    async def _run(self, handle, scope, picked, filename, user) -> dict[str, Any]:
        started = time.time()
        errors: list[dict[str, Any]] = []
        forge = None
        if FORGE in picked:
            handle.step("Downloading YARA Forge rules")
            try:
                info = await self.get_forge()
                forge = self.forge_dir / FORGE_FILE
                if info.get("error"):
                    errors.append({"words": "deepscan.error.forge_old", "values": {}})
            except DeepScanError as exc:
                picked = [s for s in picked if s != FORGE]
                errors.append({"words": "deepscan.error.forge", "values": {"why": str(exc)}})
        files = await asyncio.to_thread(self.targets, scope, filename)
        handle.step("Scanning files", total=len(files), unit="files")
        stop = threading.Event()  # one per run, so a cancelled run's thread stays stopped
        try:
            result = await asyncio.to_thread(self._scan, files, picked, forge, scope, handle, stop)
        except asyncio.CancelledError:
            stop.set()
            raise
        result.update(
            scope=scope,
            target=filename,
            started=started,
            finished=time.time(),
            by=user,
        )
        result["errors"] = errors + result["errors"]
        if scope == "folder" and "java" not in self.roots():
            result["errors"].append({"words": "deepscan.error.no_java", "values": {}})
        result["scanners"] = result.pop("picked")
        async with self.ctx.jarscan.lock:  # the Quarantine button and Undo save here too
            saved = self.store()
            for key, took in result.pop("took").items():
                saved["took"][key] = took
            saved["last"] = result
            self.save(saved)
        await self._settle(user)
        await self.ctx.bus.publish(
            Event(
                type="deep_scan_done",
                level="info",
                message=f"Deep scan finished: {len(result['found'])} file(s) to look at.",
                data={"found": len(result["found"])},
            )
        )
        return {"files": result["files"], "found": len(result["found"])}

    def _scan(self, files, picked, forge, scope, handle, stop) -> dict[str, Any]:
        """Read and check every file. Runs in a thread, below normal priority."""
        import yara_x

        with low_priority():
            errors: list[dict[str, Any]] = []
            rules = None
            if FORGE in picked:
                try:
                    rules = compile_rules(forge)
                except (yara_x.CompileError, BrokenRules) as exc:
                    # A broken download: drop it, so the next run fetches it again.
                    for name in (FORGE_FILE, FORGE_INFO):
                        with contextlib.suppress(OSError):
                            (self.forge_dir / name).unlink()
                    picked = [s for s in picked if s != FORGE]
                    errors.append({"words": "deepscan.error.forge", "values": {"why": str(exc)}})
            if rules is None and picked:
                rules = compile_rules(None)
            scanner = yara_x.Scanner(rules) if rules is not None else None
            if scanner is not None:
                scanner.set_timeout(TIMEOUT)
            use_av = scope == "folder" and antivirus.is_on()
            server_jar = self.ctx.config.server.jar.replace("\\", "/")
            found: list[dict[str, Any]] = []
            seconds = {key: 0.0 for key in picked}
            done = skipped = 0
            for root, path, rel in files:
                if stop.is_set():
                    break
                handle.advance(1)
                try:
                    size = path.stat().st_size
                    if size > MAX_FILE:
                        skipped += 1
                        continue
                    data = path.read_bytes()
                except OSError:
                    errors.append({"words": "deepscan.error.unreadable", "values": {"where": rel}})
                    continue
                done += 1
                hits: list[dict[str, Any]] = []
                known_bad = False
                if use_av:
                    av = antivirus.scan_jar(data, path.name)
                    if av:
                        known_bad = True
                        hits.append(
                            {
                                "scanner": antivirus.NAME,
                                "words": "jarscan.finding.antivirus",
                                "values": {"where": antivirus.where_words(av, rel)},
                            }
                        )
                if scope == "folder" and root == "server":
                    extra = file_check(PurePosixPath(rel), server_jar)
                    if extra:
                        hits.append(extra)
                if scanner is not None and path.suffix.lower() not in REGIONS:
                    begun = time.monotonic()
                    yara, slow = yara_findings(scanner, data)
                    for key in picked:  # both rule sets run in one pass
                        seconds[key] += (time.monotonic() - begun) / len(picked)
                    hits += yara
                    if slow:
                        errors.append({"words": "deepscan.error.slow", "values": {"where": rel}})
                if hits and len(found) < MAX_FOUND:
                    found.append(
                        {
                            "root": root,
                            "path": rel,
                            "sha256": hashlib.sha256(data).hexdigest(),
                            "kind": "known_bad" if known_bad else "worth_a_look",
                            "findings": hits,
                            "state": "found",
                        }
                    )
            took = {key: {"seconds": round(seconds[key], 1), "files": done} for key in picked}
            if stop.is_set():
                errors.append({"words": "deepscan.error.stopped", "values": {}})
            return {
                "picked": picked,
                "files": done,
                "skipped": skipped,
                "found": found,
                "errors": errors,
                "took": took,
            }

    # ------------------------------------------------------------ quarantine
    async def _settle(self, user: str) -> None:
        """Known bad files go to quarantine now, or wait for the server to
        stop (they are moved before it next starts)."""
        jarscan = self.ctx.jarscan
        async with jarscan.lock:
            saved = self.store()
            kept = set(jarscan.store()["kept"])
            for item in (saved.get("last") or {}).get("found", []):
                if item["kind"] != "known_bad" or item["state"] != "found":
                    continue
                item["state"] = "kept" if item["sha256"] in kept else "waiting"
            self.save(saved)
            if not jarscan._busy():
                await self.move_waiting(user)
        for item in (saved.get("last") or {}).get("found", []):
            if item["state"] in ("waiting", "quarantined") and item["kind"] == "known_bad":
                await self.ctx.bus.publish(
                    Event(
                        type="jar_scan_found",
                        level="error",
                        message=f"Deep scan: {antivirus.NAME} found something in "
                        f"{item['path']}. It goes to quarantine, and you can put it "
                        "back with Undo.",
                        data={
                            "filename": PurePosixPath(item["path"]).name,
                            "root": item["root"],
                            "path": item["path"],
                            "findings": item["findings"],
                        },
                    )
                )

    def waiting(self) -> bool:
        return any(
            f["state"] == "waiting" for f in (self.store().get("last") or {}).get("found", [])
        )

    async def move_waiting(self, user: str) -> list[str]:
        """Move every Known bad file waiting for quarantine. The jar
        check's lock is held by the caller."""
        saved = self.store()
        moved = []
        for item in (saved.get("last") or {}).get("found", []):
            if item["state"] != "waiting":
                continue
            if await self._move(item, user):
                moved.append(item["path"])
        self.save(saved)
        return moved

    async def _move(self, item: dict[str, Any], user: str) -> bool:
        root = self.roots().get(item["root"])
        if root is None:
            item["state"] = "gone"
            return False
        path = root / item["path"]
        if not is_inside(root, path) or path.is_symlink() or not path.is_file():
            item["state"] = "gone"
            return False
        moved = await self.ctx.jarscan.quarantine_file(
            path, item["root"], item["path"], item["sha256"], item["findings"], user
        )
        item["state"] = "quarantined" if moved else "gone"
        return moved

    async def quarantine(self, root: str, rel: str, user: str) -> dict[str, Any]:
        """The Quarantine button on a Worth a look file."""
        self.ctx.mods.require_server_offline("move a file to quarantine")
        async with self.ctx.jarscan.lock:
            saved = self.store()
            item = next(
                (
                    f
                    for f in (saved.get("last") or {}).get("found", [])
                    if f["root"] == root and f["path"] == rel
                ),
                None,
            )
            if item is None or item["state"] == "quarantined":
                raise DeepScanError("That file isn't on the deep scan's list.")
            path = self.roots().get(root, Path("/nonexistent")) / rel
            if path.is_file() and await asyncio.to_thread(_sha256, path) != item["sha256"]:
                raise DeepScanError("That file changed since the scan. Scan it again first.")
            if not await self._move(item, user):
                self.save(saved)
                raise DeepScanError("That file is gone from the folder.")
            self.save(saved)
        return self.status()

    def summary(self) -> dict[str, Any] | None:
        last = self.store().get("last")
        if not last:
            return None
        return {
            "finished": last["finished"],
            "files": last["files"],
            "known_bad": sum(1 for f in last["found"] if f["kind"] == "known_bad"),
            "worth_a_look": sum(
                1 for f in last["found"] if f["kind"] == "worth_a_look" and f["state"] == "found"
            ),
        }
