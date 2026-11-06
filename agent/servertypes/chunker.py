"""Bedrock to Java (and back) with Chunker, as a new server.

Chunker (github.com/HiveGamesOSS/Chunker, by Hive Games, MIT License)
converts worlds between the two editions. Its command-line jar is built from
source at the pinned version below by the release build
(scripts/build_chunker.py) and ships with the app as ``tools/chunker-cli.jar``,
so nothing is downloaded and it works offline. It is run with the Java
already on the PC. This is the third documented exception to house rule 2,
"Chunker world conversion, after a click" (docs/security.md):

  * it is run as an argument list, never through a shell, with fixed
    arguments: two folders this app made in its own data folder and an
    output format built from a version checked against the pinned
    Chunker's own list
  * it reads a copy of the world, so the original server is only read,
    and the result becomes a NEW server ("Survival (Java)")
  * its output is captured into a log, it gets a memory limit that fits
    the PC, and a run that doesn't leave a level.dat counts as failed

Chunker leaves out mobs, villagers, animals, armor stands, dropped items
and structure data (villages, strongholds); only paintings and item frames
come along. The owner is told so before anything happens, every time.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil

from .. import servertypes, serveruser
from ..config import PROJECT_ROOT
from ..minecraft.properties import FILENAME, PropertiesFile, world_folder
from .install import InstallError, _log_path

if TYPE_CHECKING:
    from ..core import AgentCore, ServerContext
    from ..jobs import JobHandle

log = logging.getLogger("msc.servertypes.chunker")

# The pinned Chunker release (a git tag). scripts/build_chunker.py builds
# this tag at this commit; the version ranges below are its README's.
VERSION = "1.21.0"
COMMIT = "50aaf6d82e8b68929279161b790d95fdddfe0a40"
JAR = PROJECT_ROOT / "tools" / "chunker-cli.jar"
MIN_JAVA = 17
TIMEOUT = 3 * 3600  # a big world takes a while on a small PC
WORK = "world-change"
_V = tuple[int, int, int]
RANGES: dict[str, list[tuple[_V, _V]]] = {
    "java": [
        ((1, 8, 8), (1, 8, 8)),
        ((1, 9, 0), (1, 9, 3)),
        ((1, 10, 0), (1, 10, 2)),
        ((1, 11, 0), (1, 11, 2)),
        ((1, 12, 0), (1, 12, 2)),
        ((1, 13, 0), (1, 13, 2)),
        ((1, 14, 0), (1, 14, 4)),
        ((1, 15, 0), (1, 15, 2)),
        ((1, 16, 0), (1, 16, 5)),
        ((1, 17, 0), (1, 17, 1)),
        ((1, 18, 0), (1, 18, 2)),
        ((1, 19, 0), (1, 19, 4)),
        ((1, 20, 0), (1, 20, 6)),
        ((1, 21, 0), (1, 21, 11)),
        ((26, 1, 0), (26, 3, 99)),
    ],
    "bedrock": [
        ((1, 12, 0), (1, 12, 0)),
        ((1, 13, 0), (1, 13, 0)),
        ((1, 14, 0), (1, 14, 60)),
        ((1, 16, 0), (1, 16, 220)),
        ((1, 17, 0), (1, 17, 40)),
        ((1, 18, 0), (1, 18, 30)),
        ((1, 19, 0), (1, 19, 80)),
        ((1, 20, 0), (1, 20, 80)),
        ((1, 21, 0), (1, 21, 130)),
        ((1, 26, 0), (1, 26, 60)),
    ],
}
# Shown before every conversion, and it can't be skipped.
LEFT_OUT = (
    "mobs",
    "villagers",
    "animals",
    "armor stands",
    "dropped items",
    "structure data such as villages and strongholds",
)
WARNING = (
    "Changing a world between Bedrock and Java leaves these out: "
    + ", ".join(LEFT_OUT)
    + ". Only paintings and item frames come along. The original server isn't changed."
)


def _parts(version: str) -> _V | None:
    """(1, 21, 50) from "1.21.50.07" (Bedrock) or "1.21.50"; None for a
    snapshot or anything else that isn't a release number."""
    found = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.\d+)?", (version or "").strip())
    if not found:
        return None
    return int(found.group(1)), int(found.group(2)), int(found.group(3) or 0)


def output_format(edition: str, version: str) -> str:
    """Chunker's name for a world format ("JAVA_1_21_1"), only for a version
    the pinned Chunker lists. Chunker itself would quietly pick the nearest
    one, so anything else is refused here, with the reason."""
    parts = _parts(version)
    if parts is None or not any(low <= parts <= high for low, high in RANGES[edition]):
        name = "Java" if edition == "java" else "Bedrock"
        raise InstallError(
            f"Chunker {VERSION} can't change {name} {version} worlds, so nothing was made. "
            "It handles the versions listed on github.com/HiveGamesOSS/Chunker."
        )
    numbers = parts if edition == "bedrock" or parts[2] else parts[:2]
    return f"{edition.upper()}_" + "_".join(str(n) for n in numbers)


def memory_mb() -> int:
    """A quarter of the PC's memory, between 1 and 4 GB, so a weak mini PC
    keeps enough for Windows (and any server still running)."""
    total = int(psutil.virtual_memory().total / (1024 * 1024))
    return max(1024, min(4096, total // 4 // 256 * 256))


def command(java: str, jar: Path, source: Path, output: Path, fmt: str) -> list[str]:
    """Chunker's argument list. Every value is this app's own: the Java the
    PC uses, the shipped jar, two folders it made and a checked format."""
    return [
        java,
        f"-Xmx{memory_mb()}M",
        "-jar",
        str(jar),
        "-i",
        str(source),
        "-o",
        str(output),
        "-f",
        fmt,
    ]


async def run(ctx: ServerContext, java: str, source: Path, output: Path, fmt: str) -> Path:
    """Run Chunker once. Returns its log; raises with the log's end when it
    fails or leaves no world behind."""
    from ..winproc import NO_WINDOW

    argv = command(java, JAR, source, output, fmt)
    log_file = _log_path(ctx)
    log.info("running Chunker on a copy of %s's world", ctx.name)
    try:
        # Chunker reads the copy in work/in and writes work/out: both inside
        # output.parent, the only folder it may change.
        process = await serveruser.start(
            ctx.config,
            argv,
            cwd=output.parent,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=NO_WINDOW,
        )
    except (serveruser.ServerUserMissing, serveruser.FolderRightsFailed) as exc:
        raise InstallError(str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise InstallError(f"Chunker couldn't be started with Java: {exc}") from exc
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout=TIMEOUT)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise InstallError("Changing the world took too long and was stopped.") from None
    text = (out or b"").decode("utf-8", errors="replace")
    log_file.write_text(f"$ {' '.join(argv)}\n(in {output.parent})\n\n{text}\n", encoding="utf-8")
    # Chunker can say "Failed to find suitable reader" and still exit with 0.
    if process.returncode != 0 or not (output / "level.dat").is_file():
        tail = "\n".join(text.strip().splitlines()[-6:])
        raise InstallError(
            f"Chunker couldn't change the world. The full log is at {log_file}.\n{tail}"
        )
    return log_file


def check_java(java: str) -> None:
    from ..minecraft.java import detect_java

    found = detect_java(java)
    if (found.version_major or 0) < MIN_JAVA:
        have = f"Java {found.version_major}" if found.version_major else "no Java"
        raise InstallError(
            f"Chunker needs Java {MIN_JAVA} or newer, and this PC has {have} "
            f"({found.error or found.path or java}). Install Java {MIN_JAVA} or newer, then "
            "try again."
        )


def suggestion(core: AgentCore, ctx: ServerContext) -> dict[str, Any]:
    """The new server's name and folder, next to the original. Only
    suggestions: the owner can change both."""
    edition = "Bedrock" if ctx.config.server_type.edition == "java" else "Java"
    base = f"{ctx.name} ({edition})"
    taken = {other.name.lower() for other in core.servers.values()}
    name, number = base, 2
    while name.lower() in taken:
        name, number = f"{base} {number}", number + 1
    folder, number = ctx.config.server_dir.parent / name, 2
    while folder.exists():
        folder, number = ctx.config.server_dir.parent / f"{name} {number}", number + 1
    return {"name": name, "directory": str(folder), "warning": WARNING, "left_out": LEFT_OUT}


def _copy_world(ctx: ServerContext, into: Path) -> Path:
    """A copy of the world laid out the way Chunker reads it (single-player:
    the Nether and End inside). The original is only read."""
    from ..worldimport import _download_members, _folder_members

    base = ctx.config.server_dir
    server_type = ctx.config.server_type
    path = base / FILENAME
    values = PropertiesFile.read(path).values() if path.is_file() else {}
    world = world_folder(base, values, server_type)
    if not (world / "level.dat").is_file():
        raise InstallError(f"{ctx.name} has no world yet, so there's nothing to change.")
    if server_type.edition == "bedrock":
        members = [(p, f"{world.name}/{name}") for p, name in _folder_members(world)]
    else:
        members = _download_members(base, world.name, server_type.split_dimensions)
    for source, name in members:
        target = into / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return into / world.name


async def convert(
    core: AgentCore,
    ctx: ServerContext,
    *,
    type_id: str,
    minecraft: str,
    name: str,
    directory: str,
    eula_accepted: bool,
    understood: bool,
    user: str = "system",
) -> dict[str, Any]:
    """Make a new server of the other edition from a copy of this world."""
    from .change import DIMENSIONS, kept_version
    from .create import create_server

    current = ctx.config.server_type
    target = servertypes.get(type_id)
    if target.edition == current.edition:
        raise InstallError("Both are the same edition: use Change server type instead.")
    if not understood:
        raise InstallError(WARNING + " Tick the box to say you understand.")
    output_format(current.edition, kept_version(ctx))  # Chunker can read it
    fmt = output_format(target.edition, minecraft)
    if ctx.server.running:
        raise InstallError(f"Stop {ctx.name} first, so its world is saved, then try again.")
    if not JAR.is_file():
        raise InstallError(
            "This copy of Command Block doesn't have Chunker. It comes with the installed app."
        )
    java = core.config.server.java
    await asyncio.to_thread(check_java, java)
    work = core.config.data_dir / WORK / time.strftime("%Y%m%d-%H%M%S")
    output = work / "out"

    async def place(new_ctx: ServerContext, folder: Path, job: JobHandle | None) -> dict[str, Any]:
        if job:
            job.step("Changing the world with Chunker (this can take a while)")
        source = await asyncio.to_thread(_copy_world, ctx, work / "in")
        log_file = await run(ctx, java, source, output, fmt)
        path = folder / FILENAME
        values = PropertiesFile.read(path).values() if path.is_file() else {}
        world = world_folder(folder, values, target)
        if world.exists():
            raise InstallError(f"The new server already has a world at {world}.")
        world.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(output), str(world))
        if target.split_dimensions:
            for dim, suffix in DIMENSIONS.items():
                if (world / dim).is_dir():
                    (folder / f"{world.name}{suffix}").mkdir(exist_ok=True)
                    shutil.move(str(world / dim), str(folder / f"{world.name}{suffix}" / dim))
        return {"chunker_log": str(log_file), "converted_from": ctx.server_id}

    try:
        return await create_server(
            core,
            name=name,
            directory=directory,
            type_id=target.id,
            minecraft=minecraft,
            eula_accepted=eula_accepted,
            user=user,
            after_install=place,
            title=f"Making {name} from {ctx.name}'s world",
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
