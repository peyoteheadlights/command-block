"""Test servers: a throwaway server to try the owner's own builds on.

A Test server is a normal server with ``server.test`` set: it gets no
default schedules, and the Discord bot doesn't list it. "Use my build
folder" names a folder (a plugin's build/libs, say); each time the server
starts, the jars in it are copied into the server's mods or plugins folder.
Nothing in the folder is ever run, and each jar gets the same checks as an
add-on added from a file: the size cap, the safety notes' known bad list,
and the right kind for this server (agent/mods/manager.py).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .config import PROJECT_ROOT
from .mods.jarinfo import read_mod_jar
from .mods.manager import MAX_FILE_BYTES, ModError, refuse_known_bad
from .security.paths import PathSafetyError, assert_not_symlink, is_inside, system_folders

if TYPE_CHECKING:
    from .core import ServerContext

log = logging.getLogger("msc.testserver")

# A build folder with more jars than this is not one plugin's output.
MAX_JARS = 20


def check_build_folder(ctx: ServerContext, value: str) -> str:
    """The folder as saved, or "" to turn it off. Raises PathSafetyError
    with a plain reason."""
    raw = (value or "").strip()
    if not raw:
        return ""
    if not ctx.config.server.test:
        raise PathSafetyError("Only a Test server can use a build folder.")
    if "\0" in raw:
        raise PathSafetyError("That isn't a folder name.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise PathSafetyError("Choose the whole path to the folder, for example C:\\dev\\build")
    if not path.is_dir():
        raise PathSafetyError(f"{path} isn't a folder on this PC.")
    assert_not_symlink(path)
    resolved = path.resolve()
    for system in system_folders():
        if is_inside(system, resolved):
            raise PathSafetyError(f"{path} is inside a system folder ({system}).")
    for folder in (ctx.config.server_dir, ctx.config.data_dir, PROJECT_ROOT):
        folder = Path(folder)
        if is_inside(folder, resolved) or is_inside(resolved, folder):
            raise PathSafetyError(f"{path} overlaps {folder}. Pick your build's own folder.")
    return str(resolved)


def copy_builds(ctx: ServerContext) -> list[str]:
    """Copy the build folder's jars in. Returns what was copied; a jar that
    is the same as the one already there is skipped. Raises ModError or
    OSError with the reason the start should show."""
    value = ctx.config.server.build_folder.strip()
    if not ctx.config.server.test or not value:
        return []
    source = Path(value)
    if not source.is_dir():
        raise ModError(f"The build folder {source} isn't there, so nothing was copied.")
    jars = sorted(
        p
        for p in source.iterdir()
        if p.suffix.lower() == ".jar" and p.is_file() and not p.is_symlink()
    )
    if len(jars) > MAX_JARS:
        raise ModError(f"The build folder has more than {MAX_JARS} jars, so nothing was copied.")
    mods = ctx.mods
    target_dir = ctx.config.mods_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for jar in jars:
        if jar.stat().st_size > MAX_FILE_BYTES:
            raise ModError(f"{jar.name} is bigger than the 300 MB limit, so it wasn't copied.")
        data = jar.read_bytes()
        if not data.startswith(b"PK"):
            raise ModError(f"{jar.name} isn't a real .jar file, so it wasn't copied.")
        target = target_dir / jar.name
        if target.is_file() and target.read_bytes() == data:
            continue
        refuse_known_bad(data, jar.name)
        part = target_dir / f".{jar.name}.part"
        part.write_bytes(data)
        try:
            mods._check_loader(read_mod_jar(part))
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)
        copied.append(jar.name)
    if copied:
        mods._cache = None
    return copied
