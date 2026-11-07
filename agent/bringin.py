"""Bring a server in: a server folder or zip from somewhere else (a rented
host's download, an old PC, a CurseForge pack's server files) becomes a
Command Block server.

  1. read it: the type, from the files it has (start scripts are read as
     text, never run), its add-ons and worlds, and its size
  2. the owner sees that ("Paper, 14 plugins, 1 world") and says go
  3. copy it into a new, empty folder as one job. The original folder or
     zip is never changed. A zip goes through the zip-slip check, may hold
     no links, and has to fit on the disk.
  4. register it, with a free port if its own one is taken
  5. a Forge or NeoForge pack that ships only its installer (CurseForge
     server zips do) or a Fabric one without its launcher: the official
     download is set up the way a new server's is
  6. make the first backup

An unknown type comes in as a custom jar based on a type the owner picks
(agent/servertypes/custom.py). The Minecraft version its files name is
only "pending" until the console prints it, as for a new server.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import servertypes
from .config import PROJECT_ROOT
from .duplicate import LEFT_BEHIND, _check_copy, _copy_files
from .minecraft import properties
from .ports import read_properties_port
from .security.paths import (
    PathSafetyError,
    assert_not_symlink,
    check_archive_member,
    check_new_server_folder,
    is_inside,
    zip_member_is_symlink,
)
from .servertypes import custom
from .servertypes.create import default_memory_mb, new_server_id, write_eula
from .servertypes.install import find_args_file, install_plan
from .servertypes.versions import _neoforge_minecraft, make_plan

if TYPE_CHECKING:
    from .core import AgentCore
    from .jobs import Job, JobHandle

log = logging.getLogger("msc.bringin")

MAX_MEMBERS = 500_000
MAX_TEXT = 64 * 1024
# Room kept free on the disk after the copy, for the first backup to start.
SPARE_BYTES = 1024**3
# Text files a pack's start script or settings might name its loader in.
TEXT_SUFFIXES = (".bat", ".cmd", ".sh", ".ps1", ".txt", ".cfg", ".properties")
# Read as text only. CurseForge server packs: variables.txt says
# MINECRAFT_VERSION=1.20.1, MODLOADER=Forge, MODLOADER_VERSION=47.2.0; others
# name the installer jar in their start script.
FORGE_INSTALLER = re.compile(r"forge-(\d+\.\d+(?:\.\d+)?)-(\d+\.\d+\.\d+)-installer\.jar", re.I)
NEOFORGE_INSTALLER = re.compile(
    r"neoforge-(\d+\.\d+\.\d+(?:\.\d+)?(?:-beta)?)-installer\.jar", re.I
)
VARIABLE = re.compile(r"^\s*(?:set\s+)?([A-Z_]+)\s*=\s*\"?([\w.+\-]{1,64})\"?\s*$", re.M | re.I)
MC_IN_HISTORY = re.compile(r"\(MC: ([\w.\-]{1,32})\)")
LIBRARY = re.compile(r"^libraries/net/(minecraftforge/forge|neoforged/neoforge)/([^/]+)/")
BUNDLED = re.compile(r"^versions/([\w.\-]{1,32})/server-[\w.\-]+\.jar$")


class BringInError(RuntimeError):
    """The folder or zip can't be brought in as asked. Written for people."""


@dataclass
class Found:
    """What a server folder or zip holds, read before anything is copied."""

    source: str  # "folder" or "zip"
    path: str
    type: str | None  # None: not a type the app knows, so a custom jar
    type_name: str | None
    jar: str = ""  # the jar it runs from, inside the server folder
    jars: list[str] = field(default_factory=list)  # every jar at its top level
    minecraft: str | None = None  # what its files say, never confirmed
    loader: str | None = None
    # Forge, NeoForge or Fabric software that still has to be set up.
    needs_install: bool = False
    content: str | None = None
    addons: int = 0
    worlds: int = 0
    size_bytes: int = 0
    files: int = 0
    problem: str | None = None  # why it can't be brought in

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "can_bring_in": self.problem is None,
            "summary": summary_words(self),
        }


# ----------------------------------------------------------------------
# reading
# ----------------------------------------------------------------------
def _root(names: list[str]) -> str:
    """The folder inside a zip that is the server: one top folder holding
    everything, or "" when the zip's top level is the server."""
    tops = {n.split("/", 1)[0] for n in names if n.strip("/")}
    if len(tops) == 1 and all("/" in n for n in names if n.strip("/")):
        return tops.pop()
    return ""


def _versions_from(names: set[str], texts: str) -> tuple[str | None, str | None, str | None, bool]:
    """(type, minecraft, loader, needs_install) for the mod loaders, from
    the installed libraries or, failing that, a pack's start files."""
    for name in names:
        if m := LIBRARY.match(name):
            build = m.group(2)
            if m.group(1).endswith("forge/forge"):
                return "forge", build.partition("-")[0], build, False
            return "neoforge", _neoforge_minecraft(build), build, False
    variables = {k.upper(): v for k, v in VARIABLE.findall(texts)}
    loader_name = variables.get("MODLOADER", "").lower()
    minecraft = variables.get("MINECRAFT_VERSION")
    loader = variables.get("MODLOADER_VERSION")
    if m := NEOFORGE_INSTALLER.search(texts):
        return "neoforge", _neoforge_minecraft(m.group(1)), m.group(1), True
    if m := FORGE_INSTALLER.search(texts):
        return "forge", m.group(1), f"{m.group(1)}-{m.group(2)}", True
    if loader_name == "forge" and minecraft and loader:
        return "forge", minecraft, f"{minecraft}-{loader}", True
    if loader_name == "neoforge" and loader:
        return "neoforge", _neoforge_minecraft(loader) or minecraft, loader, True
    if loader_name in ("fabric", "quilt"):
        return loader_name, minecraft, loader, True
    return None, minecraft, None, False


def detect(names: set[str], read) -> dict[str, Any]:
    """The type a server is, from its file names and a few small files
    (``read(name)`` gives a file's text or ""). Pure: nothing is run."""
    top = {n.split("/", 1)[0] for n in names}
    top_files = {n for n in names if "/" not in n}
    jars = sorted(n for n in top_files if n.lower().endswith(".jar"))
    lowered = {j.lower(): j for j in jars}
    texts = "\n".join(
        [*top_files, *(read(n) for n in sorted(top_files) if n.lower().endswith(TEXT_SUFFIXES))]
    )
    history = read("version_history.json") if "version_history.json" in top_files else ""
    minecraft = None
    if m := MC_IN_HISTORY.search(history):
        minecraft = m.group(1)
    for name in names:
        if m := BUNDLED.match(name):
            minecraft = minecraft or m.group(1)

    def pick(*prefixes: str) -> str:
        return next((lowered[j] for j in sorted(lowered) if j.startswith(prefixes)), "")

    if {"bedrock_server.exe", "bedrock_server"} & top_files:
        return {"type": "bedrock", "jar": "bedrock_server.exe", "jars": jars}
    loader_type, mc, loader, needs = _versions_from(names, texts)
    if loader_type in ("forge", "neoforge"):
        return {"type": loader_type, "minecraft": mc, "loader": loader, "needs_install": needs}
    if "fabric-server-launch.jar" in top_files or ".fabric" in top:
        return {"type": "fabric", "jar": "fabric-server-launch.jar", "minecraft": minecraft or mc}
    if "quilt-server-launch.jar" in top_files or ".quilt" in top:
        return {"type": "quilt", "jar": "quilt-server-launch.jar", "minecraft": minecraft or mc}
    if loader_type:  # a Fabric or Quilt pack without its launcher yet
        return {"type": loader_type, "minecraft": mc, "loader": loader, "needs_install": True}
    for type_id, marker in (
        ("leaf", "config/leaf-global.yml"),
        ("purpur", "purpur.yml"),
        ("paper", "config/paper-global.yml"),
    ):
        named = pick(type_id)
        said = type_id.capitalize() in history
        if named or said or marker in names:
            jar = named or pick("paper", "purpur", "leaf") or (jars[0] if len(jars) == 1 else "")
            return {"type": type_id, "jar": jar, "jars": jars, "minecraft": minecraft}
    if "server.jar" in top_files and len(jars) == 1:
        return {"type": "vanilla", "jar": "server.jar", "minecraft": minecraft}
    return {"type": None, "jar": jars[0] if len(jars) == 1 else "", "jars": jars}


def _count(names: set[str], found: dict[str, Any]) -> tuple[str | None, int, int]:
    type_id = found.get("type")
    server_type = servertypes.get(type_id) if type_id else None
    folder = server_type.content_folder if server_type else None
    if server_type is None:  # a custom jar: count whichever it has
        folder = "plugins" if any(n.startswith("plugins/") for n in names) else "mods"
    addons = sum(
        1
        for n in names
        if folder
        and n.startswith(f"{folder}/")
        and n.count("/") == 1
        and n.lower().endswith(".jar")
    )
    root = "worlds/" if type_id == "bedrock" else ""
    depth = root.count("/") + 1
    worlds = {
        n.rsplit("/", 1)[0]
        for n in names
        if n.startswith(root) and n.endswith("/level.dat") and n.count("/") == depth
    }
    return folder, addons, len(worlds)


def _found(source: str, path: Path, names: set[str], read, size: int) -> Found:
    found = detect(names, read)
    folder, addons, worlds = _count(names, found)
    type_id = found.get("type")
    result = Found(
        source=source,
        path=str(path),
        type=type_id,
        type_name=servertypes.get(type_id).name if type_id else None,
        jar=found.get("jar") or "",
        jars=found.get("jars") or [],
        minecraft=found.get("minecraft"),
        loader=found.get("loader"),
        needs_install=bool(found.get("needs_install")),
        content=folder,
        addons=addons,
        worlds=worlds,
        size_bytes=size,
        files=len(names),
    )
    if result.needs_install and not result.minecraft:
        result.problem = (
            f"This looks like a {result.type_name} server, but nothing in it says which "
            "Minecraft version it's for, so it can't be set up."
        )
    elif (
        not result.needs_install
        and type_id not in ("forge", "neoforge", "bedrock")
        and not (result.jars or result.jar)
    ):
        result.problem = "There's no server jar in it, so this isn't a server folder."
    return result


def inspect(core: AgentCore, value: str) -> Found:
    """Read a server folder or zip on this PC. Nothing is changed."""
    raw = (value or "").strip()
    if not raw or "\0" in raw:
        raise BringInError("Pick the server's folder or zip.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise BringInError("Pick the whole path to the server's folder or zip.")
    try:
        assert_not_symlink(path)
    except PathSafetyError as exc:
        raise BringInError(str(exc)) from exc
    if path.is_file() and path.suffix.lower() == ".zip":
        return inspect_zip(path)
    if not path.is_dir():
        raise BringInError(f"{path} isn't a folder or a .zip file.")
    resolved = path.resolve()
    for ctx in core.servers.values():
        folder = ctx.config.server_dir
        if is_inside(folder, resolved) or is_inside(resolved, folder):
            raise BringInError(
                f"{path} is the folder of the server '{ctx.name}'. Use Duplicate to copy it."
            )
    for folder in (PROJECT_ROOT, core.config.data_dir):
        if is_inside(Path(folder), resolved) or is_inside(resolved, Path(folder)):
            raise BringInError(f"{path} overlaps this app's own folder {folder}.")
    files = walk(resolved)
    names = {relative for _, relative, _ in files}

    def read(name: str) -> str:
        try:
            with open(resolved / name, "rb") as fh:
                return fh.read(MAX_TEXT).decode("utf-8", errors="replace")
        except OSError:
            return ""

    return _found("folder", resolved, names, read, sum(size for _, _, size in files))


def walk(source: Path) -> list[tuple[Path, str, int]]:
    """(file, path inside the folder, size) for everything to copy: links
    and what belongs to the old place's history (logs, crash reports, the
    world's lock) are left behind."""
    found: list[tuple[Path, str, int]] = []
    for root, dirs, names in os.walk(source, followlinks=False):
        here = Path(root)
        top = here == source
        dirs[:] = [
            d for d in dirs if not os.path.islink(here / d) and not (top and d in LEFT_BEHIND)
        ]
        for name in names:
            full = here / name
            if full.is_symlink() or name == "session.lock" or (top and name in LEFT_BEHIND):
                continue
            try:
                found.append((full, (full.relative_to(source)).as_posix(), full.stat().st_size))
            except OSError:
                continue
            if len(found) > MAX_MEMBERS:
                raise BringInError("That folder has far too many files in it to be a server.")
    return found


def zip_members(path: Path) -> tuple[list[zipfile.ZipInfo], str]:
    """The zip's files, each checked (no path out of the folder, no links),
    and the folder inside it that is the server."""
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise BringInError(f"That file isn't a zip that can be opened: {exc}") from exc
    if len(infos) > MAX_MEMBERS:
        raise BringInError("That zip has far too many files in it to be a server.")
    for info in infos:
        try:
            check_archive_member(
                path.parent / "zip-check", info.filename, zip_member_is_symlink(info)
            )
        except PathSafetyError as exc:
            raise BringInError(f"That zip isn't safe to open: {exc}") from exc
    root = _root([i.filename for i in infos])
    prefix = f"{root}/" if root else ""
    files = [i for i in infos if not i.is_dir() and i.filename.startswith(prefix)]
    return files, prefix


def inspect_zip(path: Path) -> Found:
    files, prefix = zip_members(path)
    names = {i.filename[len(prefix) :] for i in files}

    def read(name: str) -> str:
        with zipfile.ZipFile(path) as zf:
            try:
                with zf.open(prefix + name) as fh:
                    return fh.read(MAX_TEXT).decode("utf-8", errors="replace")
            except KeyError:
                return ""

    return _found("zip", path, names, read, sum(i.file_size for i in files))


def _unzip(path: Path, target: Path, advance) -> list[tuple[Path, str, int]]:
    files, prefix = zip_members(path)
    written = []
    with zipfile.ZipFile(path) as zf:
        for info in files:
            relative = info.filename[len(prefix) :]
            destination = check_archive_member(target, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            # zipfile never gives more than the size the entry declares.
            with zf.open(info) as src, open(destination, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            advance(info.file_size)
            written.append((destination, relative, info.file_size))
    return written


# ----------------------------------------------------------------------
# bringing it in
# ----------------------------------------------------------------------
def _fix_port(
    core: AgentCore, folder: Path, server_type: servertypes.ServerType
) -> tuple[int, str | None]:
    """The brought-in server's port: its own when no other server uses it,
    else a free one written to its server.properties, with a note."""
    port = read_properties_port(folder)
    if server_type.dialect == "bedrock":
        taken = port is None or ("udp", port) in core.ports.used()
        pair = core.ports.suggest_bedrock_server() if taken else None
        if not taken:
            return int(port), None  # type: ignore[arg-type]
        if pair is None:
            raise BringInError("No free pair of UDP ports was found for the Bedrock server.")
        edited = properties.PropertiesFile.read(folder / properties.FILENAME)
        edited.set("server-port", str(pair[0]))
        edited.set("server-portv6", str(pair[1]))
        edited.write(folder / properties.FILENAME)
        return pair[0], f"Port {port} was taken, so it now uses port {pair[0]}."
    owner = core.ports.used().get(("tcp", port)) if port else None
    if port and not owner:
        return port, None
    free = core.ports.suggest() or 25565
    path = folder / properties.FILENAME
    edited = properties.PropertiesFile.read(path) if path.is_file() else properties.PropertiesFile()
    edited.set("server-port", str(free))
    edited.write(path)
    if owner:
        return free, (
            f"Port {port} is used by '{core.servers[owner].name}', so this server now uses "
            f"port {free}."
        )
    return free, f"It didn't set a port, so it uses port {free}."


async def bring_in(
    core: AgentCore,
    *,
    path: str,
    name: str,
    directory: str,
    based_on: str | None = None,
    jar: str | None = None,
    trusted: bool = False,
    user: str = "system",
    software: dict[str, Any] | None = None,
) -> Job:
    """Check the request, then copy and set up the server as one job.
    ``software`` (bringing a server back from a backup, which holds no
    server software): its type and versions, set up like a new server's."""
    name = (name or "").strip()
    if not name:
        raise BringInError("Give the server a name.")
    found = await asyncio.to_thread(inspect, core, path)
    if software:
        kind = servertypes.get(software["type"])
        found.type, found.type_name = kind.id, kind.name
        found.minecraft, found.loader = software["minecraft"], software.get("loader")
        found.needs_install, found.problem = True, None
    if found.problem:
        raise BringInError(found.problem)
    chosen_jar = found.jar
    if found.type is None:
        if not based_on:
            raise BringInError(
                "The app can't tell which kind of server this is. Pick the kind it's based on, "
                "and it comes in as your own server jar."
            )
        if not trusted:
            raise BringInError(custom.WARNING + " Tick the box to say you trust it.")
        chosen_jar = (jar or found.jar or "").strip()
        if chosen_jar not in found.jars:
            raise BringInError("Pick which jar in it is the server.")
    server_type = (
        custom.check_base(based_on or "") if found.type is None else servertypes.get(found.type)
    )
    registered = [(ctx.name, ctx.config.server_dir) for ctx in core.servers.values()]
    try:
        folder = check_new_server_folder(
            directory, protected=[PROJECT_ROOT, core.config.data_dir], registered=registered
        )
    except PathSafetyError as exc:
        raise BringInError(str(exc)) from exc
    room = shutil.disk_usage(folder.parent).free
    if found.size_bytes + SPARE_BYTES > room:
        raise BringInError(
            f"It needs {found.size_bytes // 1024**2} MB and the disk has "
            f"{room // 1024**2} MB free, so it wasn't copied."
        )
    server_id = new_server_id(name, core.config.server_ids)
    existed = folder.is_dir()
    title = f"Bringing in {name}"

    async def run(job: JobHandle) -> dict[str, Any]:
        registered_here = False
        try:
            job.step("Copying files", total=found.size_bytes, unit="bytes")
            folder.mkdir(parents=True, exist_ok=True)
            source = Path(found.path)
            if found.source == "zip":
                files = await asyncio.to_thread(_unzip, source, folder, job.advance)
            else:
                files = await asyncio.to_thread(walk, source)
                await asyncio.to_thread(_copy_files, files, folder, job.advance)
            ok, detail = await asyncio.to_thread(_check_copy, files, folder)
            if not ok:
                raise BringInError(f"The copy didn't work: {detail}")
            job.step("Setting up the server")
            if software and server_type.dialect != "bedrock":
                await asyncio.to_thread(write_eula, folder, True)  # ticked in the request
            port, note = await asyncio.to_thread(_fix_port, core, folder, server_type)
            memory = 0 if server_type.dialect == "bedrock" else default_memory_mb()[0]
            entry = custom.entry(
                core, server_id, name, folder, server_type, chosen_jar, memory, port
            )
            entry["custom_jar"] = found.type is None
            if server_type.dialect == "bedrock":
                entry["jvm_args"] = []
            if server_type.launch == "args_file" and not found.needs_install:
                entry["jar"] = ""
                entry["args_file"] = find_args_file(folder, server_type.id) or ""
                if not entry["args_file"]:
                    raise BringInError(f"It has no {server_type.name} start file to run.")
            ctx = await core.add_server(entry, user=user)
            registered_here = True
            loader = found.loader
            if found.needs_install:
                plan = await make_plan(server_type.id, found.minecraft or "", found.loader)
                result = await install_plan(ctx, plan, folder, job=job)
                ctx.config.set("server.jar", result["jar"])
                ctx.config.set("server.args_file", result["args_file"])
                loader = result["loader_version"]
            if server_type.content_folder:
                ctx.config.set("mods.directory", server_type.content_folder)
            ctx.config.save()
            core.db.set_server_software(server_id, server_type.id, loader)
            if found.minecraft:
                # What its files say; confirmed only once the console prints it.
                ctx.server.pending_version = {
                    "type": server_type.id,
                    "minecraft_version": found.minecraft,
                    "loader_version": loader,
                    "since": time.time(),
                }
            job.step("Making the first backup")
            backup = await ctx.backups.create(
                name="brought-in", kind="manual", note="Right after it was brought in", job=job
            )
        except BaseException:
            if registered_here:
                try:
                    await core.remove_server(server_id, user=user)
                except Exception:  # pragma: no cover - reported, never hidden
                    log.exception("could not take the half-brought-in server off the list")
            if not existed:
                shutil.rmtree(folder, ignore_errors=True)
            raise
        core.db.audit(
            "server_bring_in",
            user=user,
            target=server_id,
            detail=f"{found.source} {server_type.id}"
            + (" custom jar" if found.type is None else ""),
            server_id=server_id,
        )
        return {
            "server_id": server_id,
            "name": name,
            "directory": str(folder),
            "port": port,
            "port_note": note,
            "backup": backup.get("id"),
            "custom_jar": found.type is None,
        }

    return core.jobs.start("bring_in", title, run, server_id=None, risky=False, user=user)


def backup_folder(core: AgentCore, value: str) -> list[dict[str, Any]]:
    """The servers in an off-PC backup folder, for "Bring servers back".
    Each says whether it can come back, and why not. Nothing is changed."""
    from .backups import offsite

    raw = (value or "").strip()
    path = Path(raw).expanduser() if raw and "\0" not in raw else None
    if path is None or not path.is_absolute() or not path.is_dir():
        raise BringInError(
            "Pick the folder your backups were copied to, on a drive that's plugged in."
        )
    try:
        assert_not_symlink(path)
    except PathSafetyError as exc:
        raise BringInError(str(exc)) from exc
    found = offsite.servers_in(path.resolve())
    if not found:
        raise BringInError(f"There are no Command Block backup copies in {path}.")
    here = {ctx.name.lower() for ctx in core.servers.values()}
    for row in found:
        kind = row.get("type")
        row.setdefault("name", row["id"])
        if not kind:
            row["problem"] = (
                "Its copies were made by an older version that didn't note its type. Use "
                "Bring a server in with the server's own folder instead."
            )
        elif kind == "custom":
            row["problem"] = "It ran your own server jar, which isn't in a backup."
        else:
            try:
                row["type_name"] = servertypes.get(kind).name
            except servertypes.UnknownServerType:
                row["problem"] = f"This app doesn't know the server type {kind}."
        row["here"] = row["name"].lower() in here
    return found


async def bring_back(
    core: AgentCore,
    *,
    folder: str,
    server: str,
    name: str,
    directory: str,
    minecraft: str | None = None,
    eula_accepted: bool = False,
    user: str = "system",
) -> Job:
    """Rebuild one server from its newest off-PC copy as a new server: the
    copy is unzipped into a new folder (the backup folder is only read),
    the software is set up like a new server's, then it's backed up."""
    if not eula_accepted:
        raise BringInError(
            "Minecraft's rules (the EULA) have to be accepted before the server software can "
            "be downloaded. Tick the box to accept them."
        )
    row = next((r for r in backup_folder(core, folder) if r["id"] == server), None)
    if row is None:
        raise BringInError("That server isn't in the backup folder any more.")
    if row.get("problem"):
        raise BringInError(row["problem"])
    version = (minecraft or row.get("minecraft") or "").strip()
    if not version:
        raise BringInError("Pick the Minecraft version it ran: its copies don't say.")
    if row["type"] == "bedrock":
        from .servertypes import bedrock

        bedrock.accept_terms(core.db, user)
    return await bring_in(
        core,
        path=row["backup"],
        name=name,
        directory=directory,
        user=user,
        software={"type": row["type"], "minecraft": version, "loader": row.get("loader")},
    )


def summary_words(found: Found) -> str:
    """ "Paper 26.3, 14 plugins, 1 world", from what was read."""
    parts = [" ".join(p for p in (found.type_name or "Unknown kind", found.minecraft) if p)]
    if found.content:
        noun = "plugin" if found.content == "plugins" else "mod"
        parts.append(f"{found.addons} {noun}{'' if found.addons == 1 else 's'}")
    parts.append(f"{found.worlds} world{'' if found.worlds == 1 else 's'}")
    return ", ".join(parts)
