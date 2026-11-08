"""HeadlessMC: the real Minecraft client with no window, for Diagnose
server's join with all mods (agent/fullmods.py).

HeadlessMC (MIT, github.com/headlesshq/headlessmc) is a launcher that runs
the game without drawing anything. Its Windows launcher is one .exe that
needs no Java of its own; it downloads the Minecraft client, the mod loader
and the Java the game needs the first time. All of that is downloaded only
inside a Diagnose the owner clicked (house rule 2), the .exe checked against
its pinned checksum.

Everything it keeps (its settings, the game files, and the sign-in) stays in
``headlessmc/`` in the agent's data folder: the XDG_* folders it reads are
pointed there. An online-mode server needs a Minecraft account, so HeadlessMC
has its own "Sign in with Microsoft" (a device code, like agent/microsoft.py).
HeadlessMC keeps that sign-in itself, in its own file in that folder; Sign
out deletes the folder that holds it. Owner only (``microsoft.account``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import downloads
from .downloads import DownloadError, FileSpec

log = logging.getLogger("msc.headlessmc")

VERSION = "3.0.0-RC8"
REPO = "headlesshq/headlessmc"
EXE_URL = (
    f"https://github.com/{REPO}/releases/download/{VERSION}/headlessmc-launcher-windows-x64.exe"
)
EXE_SHA256 = "72c42ddd7240f08c79615bbf5e9bef15d209d3e75017f4ef9d9dde9f5028f935"
EXE_SIZE = 94_281_728
EXE = "headlessmc.exe"
NAME_FILE = "account.txt"
# What HeadlessMC prints while signing in.
VISIT = "To login visit "
LOGGED_IN = re.compile(r"Logged in to account (\S+) successfully!")
CODE_WAIT = 120.0  # the first sign-in downloads the launcher first
SIGN_IN_TIME = 15 * 60.0
# Its folders (XDG base directories), all inside ours.
FOLDERS = ("config", "data", "state", "cache")


class HeadlessMcError(RuntimeError):
    """Written for people."""


class HeadlessMc:
    def __init__(self, data_dir: Path) -> None:
        self.folder = Path(data_dir) / "headlessmc"
        # The sign-in being typed in: {"code", "url"} or {"error"}.
        self.pending: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None

    @property
    def exe(self) -> Path:
        return self.folder / EXE

    @property
    def game_dir(self) -> Path:
        return self.folder / "game"

    async def ensure(self) -> Path:
        """The launcher, downloaded and checked the first time."""
        if self.exe.is_file() and self.exe.stat().st_size == EXE_SIZE:
            return self.exe
        try:
            with downloads.allow_releases(REPO):
                fetched = await downloads.download(
                    FileSpec(url=EXE_URL, name=EXE, sha256=EXE_SHA256, size=EXE_SIZE),
                    self.folder,
                )
        except DownloadError as exc:
            raise HeadlessMcError(f"HeadlessMC couldn't be downloaded: {exc}") from exc
        return fetched.path

    def argv(self, *command: str) -> list[str]:
        """The launcher with our settings (no update check, plain output, one
        game folder) and ``command``. Settings go before the command."""
        return [
            str(self.exe),
            "-Dhmc.jline.enabled=false",
            "-Dhmc.updater.enabled=false",
            f"-Dhmc.files.game={self.game_dir}",
            "-Dhmc.files.game-for-each-version=false",
            *command,
        ]

    def env(self) -> dict[str, str]:
        found = dict(os.environ)
        for name in FOLDERS:
            found[f"XDG_{name.upper()}_HOME"] = str(self.folder / name)
        return found

    @contextlib.asynccontextmanager
    async def started(self, *command: str) -> AsyncIterator[asyncio.subprocess.Process]:
        """Run HeadlessMC with ``command``; on leaving, it and the game it
        started are stopped."""
        from .winproc import NO_WINDOW

        await self.ensure()
        self.folder.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            *self.argv(*command),
            cwd=str(self.folder),
            env=self.env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=NO_WINDOW,
        )
        try:
            yield process
        finally:
            stop_tree(process.pid)
            if process.returncode is None:
                process.kill()
            await process.wait()

    # ------------------------------------------------------------ sign-in
    def name(self) -> str | None:
        """The account HeadlessMC signed in with, or None."""
        try:
            return (self.folder / NAME_FILE).read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def status(self) -> dict[str, Any]:
        pending = self.pending or {}
        return {
            "signed_in": self.name() is not None,
            "name": self.name(),
            "code": pending.get("code"),
            "url": pending.get("url"),
            "error": pending.get("error"),
        }

    async def sign_in(self) -> dict[str, Any]:
        """Start HeadlessMC's sign-in and answer once it shows the page to
        open (or failed). It keeps waiting for the owner in the background."""
        if self._task and not self._task.done() and (self.pending or {}).get("code"):
            return self.status()
        self.cancel()
        self.pending = {}
        got_code = asyncio.Event()

        def line(text: str) -> None:
            if text.startswith(VISIT):
                url = text[len(VISIT) :].strip()
                code = parse_qs(urlparse(url).query).get("otc", [""])[0]
                if url.startswith("https://"):
                    self.pending = {"code": code or None, "url": url}
                    got_code.set()
            elif found := LOGGED_IN.search(text):
                (self.folder / NAME_FILE).write_text(found.group(1), encoding="utf-8")
                self.pending = None
                got_code.set()

        async def go() -> None:
            try:
                await asyncio.wait_for(self._lines(("account", "login"), line), SIGN_IN_TIME)
            except TimeoutError:
                self.pending = {"error": "The code ran out before it was typed in. Try again."}
            except (HeadlessMcError, OSError) as exc:
                self.pending = {"error": str(exc)}
            if self.pending == {} or (self.pending or {}).get("code"):
                self.pending = {"error": "The sign-in stopped before it finished. Try again."}
            got_code.set()

        self._task = asyncio.create_task(go())
        try:
            await asyncio.wait_for(got_code.wait(), CODE_WAIT)
        except TimeoutError:
            self.cancel()
            self.pending = {"error": "HeadlessMC didn't answer. Check this PC's internet."}
        return self.status()

    async def _lines(self, command: tuple[str, ...], on_line: Callable[[str], None]) -> None:
        async with self.started(*command) as process:
            assert process.stdout is not None
            async for raw in process.stdout:
                on_line(raw.decode("utf-8", "replace").strip())

    def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.pending = None

    def sign_out(self) -> None:
        """Delete HeadlessMC's settings and sign-in; the game files stay."""
        self.cancel()
        (self.folder / NAME_FILE).unlink(missing_ok=True)
        for name in ("config", "state"):
            shutil.rmtree(self.folder / name, ignore_errors=True)


def stop_tree(pid: int) -> None:
    """Stop the launcher's children (the game), which outlive it otherwise."""
    import psutil

    try:
        children = psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        return
    for child in children:
        with contextlib.suppress(psutil.Error):
            child.kill()
