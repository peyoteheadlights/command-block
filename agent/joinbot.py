"""The test player that "Diagnose server" joins with.

Joining like a real player means speaking the whole game protocol for every
Minecraft version. We don't write that ourselves: Mineflayer (a Node.js bot
library, MIT) does it, and ``bot/join.js`` is our small script around it.

It ships with the app in ``tools/diagnose``: node.exe (Node.js for Windows)
and the bot's packages, put there when the installer is built
(scripts/build_bot.py). That build downloads each file and checks it against
the checksum pinned here or in ``bot/package-lock.json``, and unpacks the
packages itself (no npm, no install scripts run). The Bedrock game data in
minecraft-data (about 400 MB) is left out until Bedrock joins need it.

``join`` runs the script once and reads one JSON line per step.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sys
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .downloads import DownloadError, FileSpec, download
from .security.paths import check_archive_member

log = logging.getLogger("msc.joinbot")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOT_SOURCE = PROJECT_ROOT / "bot"
# Where the installed app has it (packaging/app.spec), and a developer's build.
SHIPPED = PROJECT_ROOT / "tools" / "diagnose"
BUILT = PROJECT_ROOT / "build" / "tools" / "diagnose"
# Node.js for Windows, pinned: the checksum is from nodejs.org's SHASUMS256.txt.
NODE_VERSION = "24.22.2"
NODE_ZIP = f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-win-x64.zip"
NODE_ZIP_SHA256 = "ca1bff5d8ead13c9f2ea404a176db46bd430704d5b5113734049fa365e7799e2"
NODE_EXE = f"node-v{NODE_VERSION}-win-x64/node.exe"
NODE_LICENSE = f"node-v{NODE_VERSION}-win-x64/LICENSE"
# The steps join.js reports, in order.
STEPS = ("connect", "login", "spawn", "chunks", "chat", "stay", "leave")
TIMEOUT = 240.0  # a whole join, as a last stop; join.js times out each step itself
# Bedrock's game data, one folder per Bedrock version: 400 MB the Java test
# player never reads ("common" stays, Mineflayer loads it).
BEDROCK_DATA = re.compile(r"^minecraft-data/minecraft-data/data/bedrock/\d")


class BotError(RuntimeError):
    """The test player couldn't be set up or started."""


def _lock_hash() -> str:
    return hashlib.sha256((BOT_SOURCE / "package-lock.json").read_bytes()).hexdigest()


def packages() -> list[tuple[str, str, str]]:
    """(folder under the bot, tarball URL, sha512 hex) for every package the
    lock file pins. Optional packages (none are needed by Mineflayer) are left out."""
    lock = json.loads((BOT_SOURCE / "package-lock.json").read_text(encoding="utf-8"))
    out = []
    for path, info in lock["packages"].items():
        if not path or info.get("optional") or info.get("dev"):
            continue
        kind, _, digest = str(info.get("integrity", "")).partition("-")
        if kind != "sha512" or not info.get("resolved"):
            raise BotError(f"The bot's lock file has no checksum for {path}.")
        out.append((path, info["resolved"], base64.b64decode(digest).hex()))
    return out


def node_path(folder: Path) -> Path | None:
    """The Node.js that runs the bot. Windows: the one we downloaded. Elsewhere
    (developers): the one on PATH."""
    if os.name != "nt":
        found = shutil.which("node")
        return Path(found) if found else None
    exe = folder / "node.exe"
    return exe if exe.is_file() else None


def ready(folder: Path) -> bool:
    """Everything is there, for this Node.js and this lock file."""
    try:
        stamp = json.loads((folder / "ready.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return stamp == {"node": NODE_VERSION, "lock": _lock_hash()} and (folder / "node.exe").is_file()


def _unpack_package(data: bytes, target: Path) -> None:
    """An npm tarball has everything under "package/"; it goes into ``target``."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not (member.isfile() or member.isdir()):
                continue  # npm packages have no links worth keeping
            name = member.name.split("/", 1)[1] if "/" in member.name else ""
            if not name or BEDROCK_DATA.match(f"{target.name}/{name}"):
                continue
            dest = check_archive_member(target, name)
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is not None:
                with source, open(dest, "wb") as out:
                    shutil.copyfileobj(source, out)


def _unpack_node(archive: Path, target: Path) -> None:
    """Only node.exe and its LICENSE from Node.js's zip: no npm, nothing else."""
    with zipfile.ZipFile(archive) as zf:
        for member, name in ((NODE_EXE, target.name), (NODE_LICENSE, "LICENSE")):
            with zf.open(member) as src, open(target.parent / name, "wb") as out:
                shutil.copyfileobj(src, out)


async def prepare(folder: Path) -> Path:
    """Download Node.js (Windows' node.exe) and the bot's packages into
    ``folder``, each checked against its pinned checksum. Run when the
    installer is built (scripts/build_bot.py), never by the app itself."""
    if ready(folder):
        return folder
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    try:
        fetched = await download(
            FileSpec(url=NODE_ZIP, name="node.zip", sha256=NODE_ZIP_SHA256), folder
        )
        await asyncio.to_thread(_unpack_node, fetched.path, folder / "node.exe")
        fetched.path.unlink()
        for path, url, sha512 in packages():
            fetched = await download(
                FileSpec(url=url, name="package.tgz", sha512=sha512, allow_unverified=False),
                folder,
            )
            data = fetched.path.read_bytes()
            fetched.path.unlink()
            await asyncio.to_thread(_unpack_package, data, folder / Path(path))
    except (DownloadError, OSError, KeyError, tarfile.TarError, zipfile.BadZipFile) as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise BotError(f"The test player couldn't be downloaded: {exc}") from exc
    for name in ("join.js", "package.json"):
        shutil.copyfile(BOT_SOURCE / name, folder / name)
    (folder / "ready.json").write_text(
        json.dumps({"node": NODE_VERSION, "lock": _lock_hash()}), encoding="utf-8"
    )
    return folder


def ensure() -> tuple[Path, Path]:
    """The shipped Node.js and join.js (or a developer's build). Returns (node, join.js)."""
    for folder in (SHIPPED, BUILT):
        node = node_path(folder)
        if ready(folder) and node is not None:
            return node, folder / "join.js"
    raise BotError(
        "The test player is missing from this copy of Command Block. Install Command Block "
        "again to get it back."
    )


async def join(
    host: str,
    port: int,
    username: str,
    version: str | None,
    chat_line: str,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Join once. Returns join.js's steps as it printed them; the last one
    that isn't ok says where and why it stopped."""
    from .winproc import NO_WINDOW

    node, script = ensure()
    argv = [str(node), str(script), host, str(port), username, version or "", chat_line]
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(script.parent),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=NO_WINDOW,
    )
    steps: list[dict[str, Any]] = []
    assert process.stdout is not None

    async def read() -> None:
        assert process.stdout is not None
        async for raw in process.stdout:
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            if isinstance(line, dict) and line.get("step") in STEPS:
                steps.append(line)
                if on_step:
                    on_step(line)

    why = "the test player stopped without saying why"
    try:
        await asyncio.wait_for(read(), timeout=TIMEOUT)
    except TimeoutError:
        why = "the test player took too long"
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
    last = steps[-1] if steps else None
    if last is None or (last.get("ok") is True and last["step"] != "leave"):
        # It ended between two steps: the next one is where it stopped.
        step = STEPS[STEPS.index(last["step"]) + 1] if last else "connect"
        steps.append({"step": step, "ok": False, "error": why})
    return steps


if __name__ == "__main__":  # pragma: no cover - a developer's quick check
    print(asyncio.run(join(sys.argv[1], int(sys.argv[2]), "CommandBlock", None, "test")))
