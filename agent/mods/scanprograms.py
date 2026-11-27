"""The deep scan's separate programs: MCAntiMalware, jNeedle and ClamAV.

Each is downloaded from its own GitHub release the first time someone
clicks a deep scan with it, checked against a checksum written here, and
run as its own program. None of their code is copied into this app.

  * MCAntiMalware (GPL-3, needs Java 17 or newer): a list of known bad
    plugins and mods, bundled in its jar. Run with
    ``--singleScan true --disableAutoUpdate true``.
  * jNeedle (MIT, Java 8 or newer): rules for known bad mods, from a list
    it downloads from maven.kosmx.dev. When that list can't be downloaded
    the row is hidden for a day.
  * ClamAV (GPL-2): a general virus scanner, unpacked into the agent's
    data folder. ``freshclam`` updates its signatures, at most once a day.
    It is weak on jar malware, and its row says so.

Their output is read line by line into findings that use each program's
own name for what it found. The programs read files and never change them;
quarantine stays with the deep scan (agent/mods/deepscan.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import downloads

DAY = 86400
TIMEOUT = 3 * 3600  # one program run, then it is stopped
BEFORE_START = 15 * 60  # the same before a start, so the start isn't held for hours
WINDOWS = sys.platform == "win32"  # ClamAV comes as a Windows build only
MAX_LINES = 50_000  # output kept from one run
MAX_UNPACKED = 1024 * 1024 * 1024
JNEEDLE_LIST = "https://maven.kosmx.dev/dev/kosmx/needles/version"
# Below normal priority on Windows, so the server and the PC come first.
LOW = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


@dataclass(frozen=True)
class Program:
    key: str
    name: str
    repo: str
    url: str
    file: str
    sha256: str
    size: int
    speed: str


MCANTIMALWARE = "mcantimalware"
JNEEDLE = "jneedle"
CLAMAV = "clamav"
PROGRAMS = {
    MCANTIMALWARE: Program(
        MCANTIMALWARE,
        "MCAntiMalware",
        "OpticFusion1/MCAntiMalware",
        "https://github.com/OpticFusion1/MCAntiMalware/releases/download/15.16/MCAntiMalware.jar",
        "MCAntiMalware.jar",
        "32a65d758d14328931f5c83a700858ee50499935731eb9be91906c67819710f9",
        30853003,
        "slower",
    ),
    JNEEDLE: Program(
        JNEEDLE,
        "jNeedle",
        "KosmX/jneedle",
        "https://github.com/KosmX/jneedle/releases/download/1.1.0-local/"
        "jneedle-cli-1.1.0-local-all.jar",
        "jneedle.jar",
        "c21a518c0d8fc81fc220b79139f87a8a1962b5cf95bb9438aaa3a047e7627275",
        8855394,
        "slower",
    ),
    CLAMAV: Program(
        CLAMAV,
        "ClamAV",
        "Cisco-Talos/clamav",
        "https://github.com/Cisco-Talos/clamav/releases/download/clamav-1.5.5/"
        "clamav-1.5.5.win.x64.zip",
        "clamav.zip",
        "90169f5e551010bfbd0208b50d9b2fa8988ef396cccbd46b0cc8cae03e22a4a4",
        225800047,
        "slowest",
    ),
}

# MCAntiMalware: "[DETECTED]: <path> MIGHT be infected with <platform>.<type>.<family>.<variant>"
_MCAM = re.compile(r"\[DETECTED\]: (?P<path>.+?) MIGHT be infected with (?P<name>\S+)")
_MCAM_LOOK = {"info", "potentiallyunwantedfile"}
# jNeedle: "<path> matches [SEVERE(<name>, rule=...), WARNING(...)]", or the
# set alone when it was given one file.
_NEEDLE = re.compile(r"^(?:(?P<path>.+?) matches )?\[(?P<set>.*)\]\s*$")
_NEEDLE_HIT = re.compile(r"\b(SEVERE|WARNING|INFORMATION)\(([^,)]*)")
# ClamAV: "<path>: <signature> FOUND"
_CLAM = re.compile(r"^(?P<path>.+): (?P<name>\S+) FOUND$")

Hit = tuple[str, dict[str, Any], bool]  # (path as printed, finding, Known bad)


class ProgramError(Exception):
    """A program that couldn't run. ``said()`` is the error line the apps show."""

    def __init__(self, words: str, **values: Any) -> None:
        super().__init__(words)
        self.words = words
        self.values = values

    def said(self) -> dict[str, Any]:
        return {"words": self.words, "values": self.values}


def found(program: str, name: str) -> dict[str, Any]:
    return {
        "scanner": PROGRAMS[program].name,
        "words": "deepscan.finding.named",
        "values": {"name": name[:200]},
    }


def read_mcantimalware(lines: list[str]) -> list[Hit]:
    hits = []
    for line in lines:
        match = _MCAM.search(line)
        if match:
            name = match["name"]
            kind = name.split(".")[1].lower() if name.count(".") >= 1 else ""
            hits.append((match["path"], found(MCANTIMALWARE, name), kind not in _MCAM_LOOK))
    return hits


def read_jneedle(lines: list[str], single: str | None) -> list[Hit]:
    """``single`` is the file when jNeedle was given one file."""
    hits = []
    for line in lines:
        match = _NEEDLE.match(line.strip())
        path = match and (match["path"] or single)
        if not match or not path:
            continue
        for level, name in _NEEDLE_HIT.findall(match["set"]):
            hits.append((path, found(JNEEDLE, name.strip() or level), level == "SEVERE"))
    return hits


def read_clamav(lines: list[str]) -> list[Hit]:
    hits = []
    for line in lines:
        match = _CLAM.match(line.strip())
        if match:
            hits.append((match["path"], found(CLAMAV, match["name"]), True))
    return hits


def last_line(lines: list[str]) -> str:
    return next((line.strip() for line in reversed(lines) if line.strip()), "")[:300]


async def run_program(argv: list[str], cwd: Path, timeout: float) -> tuple[int, list[str]]:
    """Run one program with a fixed argument list, no shell and no window,
    and return its exit code and output lines. Stopped, with its children,
    after ``timeout`` seconds or when the scan is cancelled."""
    from ..headlessmc import stop_tree
    from ..winproc import NO_WINDOW

    cwd.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=NO_WINDOW | LOW,
    )
    lines: list[str] = []

    async def read() -> None:
        assert process.stdout is not None
        while raw := await process.stdout.readline():
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if len(lines) < MAX_LINES:
                lines.append(line)
            else:
                lines[-1] = line  # the last line still says how it ended
        await process.wait()

    try:
        await asyncio.wait_for(read(), timeout)
    except TimeoutError:
        raise ProgramError("deepscan.error.program_timeout", minutes=round(timeout / 60)) from None
    finally:
        if process.returncode is None:
            stop_tree(process.pid)
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
    return process.returncode or 0, lines


class ScanPrograms:
    """The programs' folders under the agent's data folder, their download
    and their runs."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def home(self, key: str) -> Path:
        return self.folder / key

    def _clamav_bin(self) -> Path | None:
        try:
            info = json.loads((self.home(CLAMAV) / "installed.json").read_text(encoding="utf-8"))
            exe = self.home(CLAMAV) / str(info["bin"]) / "clamscan.exe"
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return exe.parent if exe.is_file() else None

    def _bin(self) -> Path:
        found_bin = self._clamav_bin()
        if found_bin is None:
            raise ProgramError("deepscan.error.not_set_up", name=PROGRAMS[CLAMAV].name)
        return found_bin

    def installed(self, key: str) -> bool:
        if key == CLAMAV:
            return self._clamav_bin() is not None
        return (self.home(key) / PROGRAMS[key].file).is_file()

    def available(self, key: str) -> bool:
        """False for a day after jNeedle's list couldn't be downloaded, and
        for ClamAV off Windows."""
        if key == CLAMAV:
            return WINDOWS
        if key != JNEEDLE:
            return True
        try:
            failed = float((self.home(JNEEDLE) / "list-failed").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        return time.time() - failed >= DAY

    def _list_failed(self) -> ProgramError:
        self.home(JNEEDLE).mkdir(parents=True, exist_ok=True)
        (self.home(JNEEDLE) / "list-failed").write_text(str(time.time()), encoding="utf-8")
        return ProgramError("deepscan.error.jneedle_list")

    # ------------------------------------------------------------ download
    async def install(self, key: str) -> None:
        program = PROGRAMS[key]
        home = self.home(key)
        try:
            with downloads.allow_releases(program.repo):
                fetched = await downloads.download(
                    downloads.FileSpec(
                        url=program.url,
                        name=program.file,
                        sha256=program.sha256,
                        size=program.size,
                        allow_unverified=False,
                    ),
                    home,
                )
            if key == CLAMAV:
                await asyncio.to_thread(self._unpack_clamav, fetched.path)
        except (downloads.DownloadError, OSError, zipfile.BadZipFile, ValueError) as exc:
            raise ProgramError(
                "deepscan.error.program_download", name=program.name, why=str(exc)[:300]
            ) from exc

    def _unpack_clamav(self, archive: Path) -> None:
        from ..security.paths import check_archive_member, zip_member_is_symlink

        target = self.home(CLAMAV) / "program"
        shutil.rmtree(target, ignore_errors=True)
        (self.home(CLAMAV) / "installed.json").unlink(missing_ok=True)
        total = 0
        found_bin = None
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                destination = check_archive_member(
                    target, info.filename, zip_member_is_symlink(info)
                )
                if info.is_dir():
                    continue
                total += info.file_size
                if total > MAX_UNPACKED:
                    raise ValueError("ClamAV's zip unpacks to more than 1 GB")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(destination, "wb") as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
                if destination.name.lower() == "clamscan.exe":
                    found_bin = destination.parent
        if found_bin is None:
            raise ValueError("ClamAV's zip has no clamscan.exe")
        bin_dir = found_bin.relative_to(self.home(CLAMAV)).as_posix()
        (self.home(CLAMAV) / "installed.json").write_text(
            json.dumps({"bin": bin_dir}), encoding="utf-8"
        )
        archive.unlink(missing_ok=True)  # 215 MB no longer needed

    async def check_jneedle_list(self) -> None:
        """Ask for jNeedle's list before a clicked run, so a list that can't
        be downloaded hides jNeedle instead of failing inside it."""
        try:
            await downloads.fetch_text(JNEEDLE_LIST, max_bytes=1024)
        except downloads.DownloadError as exc:
            raise self._list_failed() from exc

    # ------------------------------------------------------------ run
    async def scan(
        self,
        key: str,
        java: str,
        paths: list[Path],
        skip: list[Path],
        download: bool,
        limit: float = TIMEOUT,
    ) -> tuple[list[Hit], list[dict[str, Any]]]:
        """Run one program over ``paths`` (folders, or one file), each run
        stopped after ``limit`` seconds. Without ``download`` nothing is
        downloaded but ClamAV's daily signatures."""
        if key == JNEEDLE and download:
            await self.check_jneedle_list()
        if not self.installed(key):
            if not download:
                raise ProgramError("deepscan.error.not_set_up", name=PROGRAMS[key].name)
            await self.install(key)
        errors: list[dict[str, Any]] = []
        if key == CLAMAV:
            errors += await self._signatures(min(limit, 1800))
        hits: list[Hit] = []
        for path in paths:
            hits += await self._one(key, java, path, skip, limit)
        return hits, errors

    async def _one(
        self, key: str, java: str, path: Path, skip: list[Path], limit: float
    ) -> list[Hit]:
        home = self.home(key)
        name = PROGRAMS[key].name
        target = str(path.resolve())
        if key == MCANTIMALWARE:
            where = "--scanFile" if path.is_file() else "--scanDirectory"
            argv = [java, "-jar", str(home / PROGRAMS[key].file), where, target]
            argv += ["--singleScan", "true", "--disableAutoUpdate", "true"]
        elif key == JNEEDLE:
            argv = [java, "-jar", str(home / PROGRAMS[key].file), "-f", target]
            argv += ["--dblocation", str(home / "list")]
        else:
            argv = [str(self._bin() / "clamscan.exe"), f"--database={home / 'db'}"]
            argv += ["--recursive", "--infected", "--no-summary", "--stdout"]
            argv.append(r"--exclude=\.(mca|mcr|mcc)$")  # world region files
            argv += [f"--exclude-dir=^{re.escape(str(s.resolve()))}" for s in skip]
            argv.append(target)
        code, lines = await run_program(argv, home, limit)
        if key == MCANTIMALWARE:
            hits = read_mcantimalware(lines)
        elif key == JNEEDLE:
            hits = read_jneedle(lines, target if path.is_file() else None)
            if any("Failed to fetch database" in line for line in lines) and not any(
                (home / "list").glob("*")
            ):
                raise self._list_failed()
        else:
            hits = read_clamav(lines)
        # ClamAV says 1 when it found something; 2 also covers files it couldn't read.
        if code != 0 and not (key == CLAMAV and code == 1) and not hits:
            raise ProgramError(
                "deepscan.error.program_failed", name=name, why=last_line(lines) or f"code {code}"
            )
        return hits

    async def _signatures(self, limit: float) -> list[dict[str, Any]]:
        """ClamAV's signatures, updated when a day old. Old ones are used
        when the update fails; with none ClamAV can't run."""
        home = self.home(CLAMAV)
        db = home / "db"
        have = any(db.glob("*.cvd")) or any(db.glob("*.cld"))
        try:
            fresh = time.time() - float((home / "signatures-at").read_text()) < DAY
        except (OSError, ValueError):
            fresh = False
        if have and fresh:
            return []
        db.mkdir(parents=True, exist_ok=True)
        conf = home / "freshclam.conf"
        conf.write_text(f"DatabaseDirectory {db}\nDatabaseMirror database.clamav.net\n")
        argv = [str(self._bin() / "freshclam.exe"), f"--config-file={conf}"]
        argv.append(f"--datadir={db}")
        try:
            code, lines = await run_program(argv, home, limit)
            why = last_line(lines) or f"code {code}"
        except ProgramError as exc:
            code, why = 1, exc.words
        if code == 0:
            (home / "signatures-at").write_text(str(time.time()))
            return []
        if have:
            return [{"words": "deepscan.error.clamav_old", "values": {"why": why}}]
        raise ProgramError("deepscan.error.clamav_none", why=why)


def is_program(key: str) -> bool:
    return key in PROGRAMS


__all__ = ["PROGRAMS", "ProgramError", "ScanPrograms", "is_program"]
