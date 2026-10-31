"""The owner's own server jar (Technical, owner only).

A custom jar is a server of a built-in type (``based_on``: logs, add-ons
and settings work like that type) whose config says ``custom_jar: true``.
The app copies the jar into the server's folder and runs it like any other
server jar, after the owner's own click: it never downloads, checks or
updates it. The version check and the newer-version notice skip it, a
version change is refused, and it is never shown as checked.

Only the owner can set one or replace one: the routes need SERVERS_MANAGE,
which no moderator can be given (agent/security/permissions.py).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import colors, notes, servertypes
from ..config import PROJECT_ROOT
from ..safechange import SafeChange, run_safe_change
from ..security.paths import (
    PathSafetyError,
    assert_not_symlink,
    check_new_server_folder,
    safe_filename,
)
from .create import default_memory_mb, new_server_id, write_eula, write_properties
from .install import InstallError

if TYPE_CHECKING:
    from ..core import AgentCore, ServerContext
    from ..jobs import JobHandle

MAX_JAR_BYTES = 512 * 1024 * 1024
WARNING = (
    "A custom server jar runs as a program on this PC, with the same access as "
    "Command Block. Only use a jar you trust. The app never checks or updates it."
)


def check_jar(value: str) -> Path:
    """A server jar the owner picked: a real .jar file (a zip) on this PC,
    not a link and not one the safety notes list as bad."""
    raw = (value or "").strip()
    if not raw or "\0" in raw:
        raise InstallError("Pick the server jar.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise InstallError("Pick the whole path to the server jar.")
    try:
        assert_not_symlink(path)
    except PathSafetyError as exc:
        raise InstallError(str(exc)) from exc
    if not path.is_file() or path.suffix.lower() != ".jar":
        raise InstallError(f"{path} isn't a .jar file.")
    if path.stat().st_size > MAX_JAR_BYTES:
        raise InstallError("That jar is bigger than 512 MB, so it isn't a server jar.")
    if not zipfile.is_zipfile(path):
        raise InstallError(f"{path.name} isn't a jar that can be opened.")
    bad = notes.BAD_FILES.get(notes.sha512_of(path)) if notes.BAD_FILES else None
    if bad:
        raise InstallError(f"{path.name} is on the safety notes' list of bad files: {bad['why']}")
    return path


def check_base(type_id: str) -> servertypes.ServerType:
    """The type a custom jar is based on: any Java type."""
    base = servertypes.get(type_id)
    if base.edition != "java":
        raise InstallError("A custom jar can only be based on a Java server type.")
    return base


def jar_name(path: Path) -> str:
    try:
        return safe_filename(path.name, {".jar"})
    except PathSafetyError:
        return "custom-server.jar"


async def create_custom(
    core: AgentCore,
    *,
    name: str,
    directory: str,
    jar: str,
    based_on: str,
    memory_mb: int | None = None,
    color: str | None = None,
    eula_accepted: bool = False,
    trusted: bool = False,
    user: str = "system",
) -> dict[str, Any]:
    """A new server that runs the owner's own jar. Nothing is downloaded."""
    name = (name or "").strip()
    if not name:
        raise InstallError("Give the new server a name.")
    if not trusted:
        raise InstallError(WARNING + " Tick the box to say you trust it.")
    if not eula_accepted:
        raise InstallError(
            "Minecraft's rules (the EULA) have to be accepted before a server can be "
            "created. Tick the box to accept them."
        )
    base = check_base(based_on)
    source = check_jar(jar)
    registered = [(ctx.name, ctx.config.server_dir) for ctx in core.servers.values()]
    folder = check_new_server_folder(
        directory, protected=[PROJECT_ROOT, core.config.data_dir], registered=registered
    )
    memory = int(memory_mb or default_memory_mb()[0])
    if not 1024 <= memory <= 1024 * 64:
        raise InstallError("That amount of memory isn't a sensible size for a server.")
    port = core.ports.suggest() or 25565
    server_id = new_server_id(name, core.config.server_ids)
    existed = folder.is_dir()
    target = jar_name(source)

    async def run(job: JobHandle | None) -> dict[str, Any]:
        if job:
            job.step("Copying your server jar")
        folder.mkdir(parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(shutil.copy2, source, folder / target)
            if base.content_folder:
                (folder / base.content_folder).mkdir(exist_ok=True)
            write_properties(folder, port, name)
            write_eula(folder, eula_accepted)
            ctx = await core.add_server(
                entry(core, server_id, name, folder, base, target, memory, port), user=user
            )
        except Exception:
            if not existed:
                shutil.rmtree(folder, ignore_errors=True)
            raise
        if base.content_folder:
            ctx.config.set("mods.directory", base.content_folder)
            ctx.config.save()
        core.db.set_server_software(server_id, base.id, None)
        if color:
            core.db.set_server_color(server_id, colors.normalise(color))
        return {"server_id": server_id, "name": name, "directory": str(folder), "port": port}

    job, result = await core.jobs.run(
        "server_create", f"Creating {name} (your own jar)", run, server_id=None, user=user
    )
    core.db.audit("custom_jar", user=user, target=server_id, detail=target, server_id=server_id)
    return {
        **result,
        "job_id": job.id,
        "memory_mb": memory,
        "verified": False,
        "server": core.get_server(server_id).summary(),
    }


def entry(
    core: AgentCore,
    server_id: str,
    name: str,
    folder: Path,
    base: servertypes.ServerType,
    jar: str,
    memory: int,
    port: int,
) -> dict[str, Any]:
    """The config entry of a server that runs a custom jar."""
    return {
        "id": server_id,
        "name": name,
        "directory": str(folder),
        "type": base.id,
        "jar": jar,
        "args_file": "",
        "custom_jar": True,
        "java": core.config.server.java,
        "jvm_args": [f"-Xmx{memory}M"],
        "port": port,
    }


async def replace_jar(ctx: ServerContext, jar: str, user: str = "system") -> dict[str, Any]:
    """Put a new version of the owner's jar in place: the server is stopped,
    the old jar backed up (one Undo puts it back), and the new one copied
    over it under the same name."""
    if not ctx.config.server.custom_jar:
        raise InstallError("This server doesn't run a custom jar.")
    source = check_jar(jar)
    name = ctx.config.server.jar
    target = ctx.config.server_dir / name

    async def change(job: JobHandle | None) -> dict[str, Any]:
        temp = target.with_name(f".{name}.part")
        await asyncio.to_thread(shutil.copy2, source, temp)
        os.replace(temp, target)
        return {"jar": name}

    async def check(result: dict[str, Any]) -> tuple[bool, str]:
        ok = target.is_file() and target.stat().st_size == source.stat().st_size
        return ok, f"{name} is in place" if ok else f"{name} didn't copy whole"

    plan = SafeChange(
        title=f"Replacing {name}",
        change=change,
        check=check,
        take_backup=target.is_file(),
        backup_name="pre-jar",
        backup_note=f"Before replacing {name}",
        backup_includes=[name],
        undo_scope=[name],
    )

    async def run(job: JobHandle | None) -> dict[str, Any]:
        return await run_safe_change(ctx.server, ctx.backups, plan, job=job, user=user)

    job, outcome = await ctx.core.jobs.run(
        "custom_jar",
        f"Replacing {name} on {ctx.name}",
        run,
        server_id=ctx.server_id,
        risky=True,
        user=user,
    )
    ctx.core.db.audit(
        "custom_jar", user=user, target=ctx.server_id, detail=name, server_id=ctx.server_id
    )
    return {**outcome, "job_id": job.id, "verified": False}
