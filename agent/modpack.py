"""Importing a modpack, as a new server or into one: a Modrinth pack
(.mrpack) or a CurseForge pack (a .zip with ``manifest.json``).

A .mrpack is a zip holding ``modrinth.index.json`` (the Minecraft version,
the loader and its version, and every mod file with its hashes and where to
download it) and ``overrides/`` and ``server-overrides/`` folders of files
copied in as they are (configs, scripts, small extras).

A CurseForge pack's ``manifest.json`` lists only each mod's project and file
id, and its ``overrides`` folder. Each file is asked of CurseForge through
our relay (agent/mods/curseforge.py) when the pack is read, kept in memory
only until it is imported, and downloaded from CurseForge's file servers
checked by sha1. A mod its author keeps from other apps doesn't stop the
import: it is listed under "Needs you" on the server's Mods page. A mod
CurseForge tags for the game only is left out and listed for the players.

Before anything is downloaded or changed, ``inspect`` reads the pack and
says what it holds: the Minecraft version, the loader version and the full
mod list, with what will be skipped and why. Then:

  * each mod is downloaded through the safe downloader, from Modrinth's CDN
    only (CurseForge's for its packs), and checked against the SHA-512 the
    pack lists (the sha1 CurseForge lists). A file the pack
    gives no SHA-512 for, or that isn't on Modrinth's CDN, is refused; a
    pack with any such file the server needs can't be imported
  * client-only files (``env.server`` is "unsupported", and client folders
    like resourcepacks/ in the overrides) are skipped, and the list of them
    is shown
  * the overrides are extracted with the same zip-slip check backup restore
    uses (check_archive_member): a pack with a path that would land outside
    the server folder, or a link, is refused as a whole
  * the server type and version are set up with the server type installer: a new
    server through create_server, an existing one through change_version
    when it isn't already on the pack's versions
  * importing into an existing server runs through the safe-change routine:
    the server is stopped, backed up and verified first, the add-ons it
    had are moved aside (kept, never deleted), and a failed import is put
    back from that backup
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import shutil
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from . import downloads, notes, servertypes
from .downloads import DownloadError, FileSpec
from .events import Event
from .mods.curseforge import GOOD_STATUS, LOADERS, Blocked, CurseForgeClient, version_of
from .mods.sources import SourceError
from .security.paths import (
    EXECUTABLE_EXTENSIONS,
    PathSafetyError,
    check_archive_member,
    is_inside,
    safe_filename,
    zip_member_is_symlink,
)

if TYPE_CHECKING:
    from .core import AgentCore, ServerContext
    from .jobs import JobHandle

log = logging.getLogger("msc.modpack")

INDEX = "modrinth.index.json"
MANIFEST = "manifest.json"
# A CurseForge pack's loader ("forge-47.2.0") as a .mrpack would name it.
CURSEFORGE_LOADERS = {
    "forge": "forge",
    "fabric": "fabric-loader",
    "quilt": "quilt-loader",
    "neoforge": "neoforge",
}
FOLDER_RE = re.compile(r"^[\w.-]{1,64}$")
UPLOAD_FOLDER = "modpack-uploads"
MAX_PACK_BYTES = 1024 * 1024 * 1024  # the .mrpack itself
MAX_INDEX_BYTES = 8 * 1024 * 1024
MAX_UNPACKED_BYTES = 4 * 1024 * 1024 * 1024  # all overrides, uncompressed
MAX_MEMBERS = 50_000
MAX_FILES = 2_000  # mods listed in the index
UPLOAD_KEEP_SECONDS = 24 * 3600
# Applied in this order, so a server-only file wins over the shared one.
OVERRIDE_FOLDERS = ("overrides", "server-overrides")
# Folders and files in the overrides that only the game client uses.
CLIENT_ONLY = frozenset(
    {
        "resourcepacks",
        "shaderpacks",
        "screenshots",
        "saves",
        "options.txt",
        "optionsof.txt",
        "optionsshaders.txt",
        "servers.dat",
        "servers.dat_old",
    }
)
# Mods are only taken from Modrinth's own CDN.
DOWNLOAD_HOSTS = frozenset({"cdn.modrinth.com"})
SHA512_RE = re.compile(r"^[0-9a-fA-F]{128}$")
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")

DOWNLOAD = "download"
CLIENT = "client_only"
REFUSED = "refused"
PENDING = "pending"  # a CurseForge file not asked about yet
NEEDS_YOU = "needs_you"  # its author keeps it from other apps
OPTIONAL = "optional"  # the pack turns it off
# A CurseForge pack, read and asked about, until it is imported. In memory
# only: CurseForge's answers are never saved.
READ: dict[str, Pack] = {}


class ModpackError(RuntimeError):
    """The pack can't be read or imported. Written for people."""


@dataclass
class PackFile:
    path: str
    name: str
    size: int | None
    status: str
    reason: str | None = None
    server: str | None = None  # the pack's env.server: required, optional, unsupported
    sha512: str | None = field(default=None, repr=False)
    sha1: str | None = field(default=None, repr=False)
    url: str | None = field(default=None, repr=False)
    need: dict[str, Any] | None = None  # what "Needs you" shows


@dataclass
class Pack:
    name: str
    version: str | None
    summary: str | None
    minecraft: str
    type_id: str
    type_name: str
    loader_version: str | None
    files: list[PackFile]
    overrides: int
    overrides_bytes: int
    skipped_overrides: list[str]
    problems: list[str]
    site: str = "Modrinth"
    folders: tuple[str, ...] = ("overrides", "server-overrides")

    @property
    def downloads(self) -> list[PackFile]:
        return [f for f in self.files if f.status == DOWNLOAD]

    def to_dict(self) -> dict[str, Any]:
        files = []
        for f in self.files:
            entry = asdict(f)
            for hidden in ("sha512", "sha1", "url", "need"):
                entry.pop(hidden, None)
            files.append(entry)
        return {
            "name": self.name,
            "version": self.version,
            "summary": self.summary,
            "minecraft_version": self.minecraft,
            "type": self.type_id,
            "type_name": self.type_name,
            "loader_version": self.loader_version,
            "files": files,
            "download_count": len(self.downloads),
            "download_bytes": sum(f.size or 0 for f in self.downloads),
            "client_only": [f.name for f in self.files if f.status == CLIENT],
            "refused": [
                {"name": f.name, "reason": f.reason} for f in self.files if f.status == REFUSED
            ],
            "needs_you": [f.need for f in self.files if f.need],
            "site": self.site,
            "overrides": self.overrides,
            "overrides_bytes": self.overrides_bytes,
            "skipped_overrides": self.skipped_overrides,
            "problems": self.problems,
            "can_import": not self.problems,
        }


# ----------------------------------------------------------------------
# reading a pack
# ----------------------------------------------------------------------
def _check_file(entry: Any) -> PackFile:
    """One entry of the index's "files", sorted into download, client-only
    or refused (with the reason)."""
    if not isinstance(entry, dict):
        return PackFile(path="?", name="?", size=None, status=REFUSED, reason="not readable")
    path = str(entry.get("path") or "")
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    size = entry.get("fileSize")
    size = int(size) if isinstance(size, int) and size >= 0 else None
    env: dict[str, Any] = entry["env"] if isinstance(entry.get("env"), dict) else {}
    server = str(env.get("server")) if env.get("server") else None
    found = PackFile(path=path, name=name or path, size=size, status=DOWNLOAD, server=server)
    if server == "unsupported":
        found.status, found.reason = CLIENT, "only for the game itself, not servers"
        return found
    try:
        check_archive_member(Path(tempfile.gettempdir()) / "mcsc-pack-check", path)
        safe_filename(name)
    except PathSafetyError:
        found.status, found.reason = REFUSED, "its file name or folder isn't safe to write"
        return found
    hashes: dict[str, Any] = entry["hashes"] if isinstance(entry.get("hashes"), dict) else {}
    sha512 = str(hashes.get("sha512") or "")
    if not SHA512_RE.match(sha512):
        found.status, found.reason = REFUSED, "the pack gives no SHA-512 to check it against"
        return found
    bad = notes.BAD_FILES.get(sha512.lower())
    if bad:
        found.status, found.reason = REFUSED, f"it's on the list of known bad files ({bad['why']})"
        return found
    urls = [str(u) for u in (entry.get("downloads") or []) if isinstance(u, str)]
    modrinth = [
        u for u in urls if urlparse(u).scheme == "https" and urlparse(u).hostname in DOWNLOAD_HOSTS
    ]
    if not modrinth:
        found.status, found.reason = REFUSED, "it isn't on Modrinth's download site"
        return found
    found.sha512, found.url = sha512.lower(), modrinth[0]
    return found


def _override_members(
    zf: zipfile.ZipFile, folders: tuple[str, ...] = OVERRIDE_FOLDERS
) -> list[tuple[zipfile.ZipInfo, str]]:
    """(entry, path inside the server folder) for every override file, in
    the order they are applied."""
    found = []
    for folder in folders:
        prefix = f"{folder}/"
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if not name.startswith(prefix) or info.is_dir():
                continue
            relative = name[len(prefix) :]
            if relative:
                found.append((info, relative))
    return found


def inspect(archive: Path) -> Pack:
    """Read a .mrpack or a CurseForge pack and say what importing it would
    do (a CurseForge pack's files are asked about later, in ``resolve``).
    Raises ModpackError when it isn't a usable modpack at all."""
    try:
        zf = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ModpackError(f"That file isn't a modpack this app can open ({exc}).") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ModpackError("That pack holds far more files than a modpack does.")
        names = set(zf.namelist())
        curseforge = INDEX not in names and MANIFEST in names
        index: Any
        dependencies: Any
        if curseforge:
            index = _read_json(zf, MANIFEST)
            dependencies, raw_files, folders = _from_manifest(index)
        else:
            if INDEX not in names:
                raise ModpackError(
                    f"That file has no {INDEX} or {MANIFEST}, so it isn't a Modrinth or "
                    "CurseForge modpack."
                )
            index, folders = _read_json(zf, INDEX), OVERRIDE_FOLDERS
            if not isinstance(index, dict) or index.get("game") != "minecraft":
                raise ModpackError("That pack isn't for Minecraft.")
            if index.get("formatVersion") != 1:
                raise ModpackError("That pack uses a format this app doesn't know yet.")
            dependencies, raw_files = index.get("dependencies"), index.get("files") or []
        if not isinstance(dependencies, dict) or not dependencies.get("minecraft"):
            raise ModpackError("The pack doesn't say which Minecraft version it is for.")
        try:
            server_type, loader = servertypes.for_mrpack(dependencies)
        except servertypes.UnknownServerType as exc:
            raise ModpackError(str(exc)) from exc
        from .servertypes.versions import VersionError, check_version

        try:
            minecraft = check_version(dependencies["minecraft"], "Minecraft version")
            if loader:
                loader = check_version(loader, f"{server_type.name} version")
        except VersionError as exc:
            raise ModpackError(str(exc)) from exc

        if not isinstance(raw_files, list) or len(raw_files) > MAX_FILES:
            raise ModpackError("The pack's file list couldn't be read.")
        files = [(_curseforge_file if curseforge else _check_file)(e) for e in raw_files]
        problems = []
        refused = [f for f in files if f.status == REFUSED]
        if refused:
            problems.append(
                f"{len(refused)} file(s) the server needs can't be downloaded safely: "
                + "; ".join(f"{f.name} ({f.reason})" for f in refused[:5])
            )

        overrides, size, skipped = 0, 0, []
        check_base = Path(tempfile.gettempdir()) / "mcsc-pack-check"
        for member, relative in _override_members(zf, folders):
            try:
                check_archive_member(check_base, relative, zip_member_is_symlink(member))
            except PathSafetyError:
                problems.append(
                    f"The pack has a file ({member.filename}) that would be written outside "
                    "the server's folder, so it can't be imported."
                )
                break
            top = relative.split("/", 1)[0]
            if top in CLIENT_ONLY:
                if top not in skipped:
                    skipped.append(top)
                continue
            if Path(relative).suffix.lower() in EXECUTABLE_EXTENSIONS:
                skipped.append(relative)
                continue
            overrides += 1
            size += member.file_size
        if size > MAX_UNPACKED_BYTES:
            problems.append("The pack's extra files add up to more than 4 GB unpacked.")

    version = index.get("versionId") or index.get("version")
    return Pack(
        name=str(index.get("name") or archive.stem)[:100],
        version=str(version)[:60] if version else None,
        summary=str(index.get("summary"))[:300] if index.get("summary") else None,
        minecraft=minecraft,
        type_id=server_type.id,
        type_name=server_type.name,
        loader_version=loader,
        files=files,
        overrides=overrides,
        overrides_bytes=size,
        skipped_overrides=skipped[:50],
        problems=problems,
        site="CurseForge" if curseforge else "Modrinth",
        folders=folders,
    )


def _read_json(zf: zipfile.ZipFile, name: str) -> Any:
    if zf.getinfo(name).file_size > MAX_INDEX_BYTES:
        raise ModpackError(f"The pack's {name} is far too big.")
    try:
        return json.loads(zf.read(name).decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ModpackError(f"The pack's {name} couldn't be read ({exc}).") from exc


def _from_manifest(manifest: Any) -> tuple[dict[str, Any], Any, tuple[str, ...]]:
    """A CurseForge manifest's versions (as a .mrpack names them), its files
    and its overrides folder."""
    if not isinstance(manifest, dict) or manifest.get("manifestType") != "minecraftModpack":
        raise ModpackError("That pack isn't a Minecraft modpack.")
    minecraft = manifest.get("minecraft")
    minecraft = minecraft if isinstance(minecraft, dict) else {}
    dependencies: dict[str, Any] = {"minecraft": minecraft.get("version")}
    loaders = [x for x in minecraft.get("modLoaders") or [] if isinstance(x, dict)]
    primary = next((x for x in loaders if x.get("primary")), loaders[0] if loaders else None)
    if primary:
        kind, _, version = str(primary.get("id") or "").partition("-")
        # An unknown loader name passes through, so for_mrpack refuses it by name.
        dependencies[CURSEFORGE_LOADERS.get(kind, kind or "an unknown loader")] = version
    folder = str(manifest.get("overrides") or "overrides")
    if not FOLDER_RE.match(folder) or folder in (".", ".."):
        raise ModpackError("The pack's overrides folder has a name that isn't safe.")
    return dependencies, manifest.get("files") or [], (folder,)


def _curseforge_file(entry: Any) -> PackFile:
    """One entry of a manifest's "files": a project and file id, asked of
    CurseForge later (resolve)."""
    project = entry.get("projectID") if isinstance(entry, dict) else None
    file_id = entry.get("fileID") if isinstance(entry, dict) else None
    if not isinstance(project, int) or not isinstance(file_id, int):
        return PackFile(path="?", name="?", size=None, status=REFUSED, reason="not readable")
    found = PackFile(
        path=f"{project}-{file_id}", name=f"CurseForge {project}", size=None, status=PENDING
    )
    if entry.get("required") is False:
        found.status, found.reason = OPTIONAL, "the pack turns it off"
    return found


async def resolve(pack: Pack, client: CurseForgeClient, job: JobHandle | None = None) -> None:
    """Ask CurseForge about each of a CurseForge pack's files: where to
    download it and its sha1, or why it can't be. One call or a few per
    mod, at the relay's pace."""
    pending = [f for f in pack.files if f.status == PENDING]
    loader = LOADERS.get(pack.type_id)
    for number, item in enumerate(pending, start=1):
        if job:
            job.step(f"Asking CurseForge about mod {number} of {len(pending)}")
        project, file_id = item.path.split("-")
        try:
            found = await client.file(project, file_id, pack.minecraft, loader)
        except SourceError as exc:
            raise ModpackError(str(exc)) from exc
        version = version_of(found) if found else None
        name = (version or {}).get("file", {}).get("filename") or item.name
        item.name, item.status = name, DOWNLOAD
        try:
            safe_filename(name, {".jar", ".zip"})
        except PathSafetyError:
            item.status, item.reason = REFUSED, "its file name isn't safe to write"
            continue
        if not version or version["status"] not in GOOD_STATUS:
            item.status, item.reason = REFUSED, "CurseForge doesn't offer that file any more"
        elif version["client_only"] or not name.lower().endswith(".jar"):
            item.status, item.reason = CLIENT, "only for the game itself, not servers"
        elif not version["file"]["sha1"]:
            item.status, item.reason = REFUSED, "CurseForge gives no sha1 to check it against"
        else:
            item.path, item.size = f"mods/{name}", version["file"]["size"]
            try:
                spec = await client.spec(version)
            except Blocked as exc:
                item.status, item.reason, item.need = NEEDS_YOU, str(exc), exc.need
                continue
            except SourceError as exc:
                item.status, item.reason = REFUSED, str(exc)
                continue
            item.url, item.sha1 = spec.url, spec.sha1
    refused = [f for f in pack.files if f.status == REFUSED]
    if refused:
        pack.problems.append(
            f"{len(refused)} file(s) the server needs can't be downloaded safely: "
            + "; ".join(f"{f.name} ({f.reason})" for f in refused[:5])
        )


async def read(core: AgentCore, token: str, archive: Path) -> Pack:
    """The pack behind ``token``: read, and for a CurseForge pack its files
    asked about once and kept in memory until it's imported or forgotten."""
    if token in READ:
        return READ[token]
    pack = await asyncio.to_thread(inspect, archive)
    if any(f.status == PENDING for f in pack.files) and not pack.problems:
        await resolve(pack, CurseForgeClient(core.config))
    if pack.site == "CurseForge":
        READ[token] = pack
    return pack


# ----------------------------------------------------------------------
# uploads
# ----------------------------------------------------------------------
def upload_folder(core: AgentCore) -> Path:
    folder = core.config.data_dir / UPLOAD_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def upload_path(core: AgentCore, token: str) -> Path:
    if not TOKEN_RE.match(token or ""):
        raise ModpackError("That upload isn't known. Choose the pack again.")
    path = upload_folder(core) / f"{token}.mrpack"
    if not path.is_file():
        raise ModpackError("That upload isn't there any more. Choose the pack again.")
    return path


def new_upload(core: AgentCore) -> tuple[str, Path]:
    """A fresh token and the file the upload is written to. Old uploads
    nobody imported are cleared out first."""
    folder = upload_folder(core)
    now = time.time()
    for old in folder.glob("*.mrpack*"):
        try:
            if now - old.stat().st_mtime > UPLOAD_KEEP_SECONDS:
                old.unlink()
        except OSError:
            pass
    token = secrets.token_hex(16)
    return token, folder / f"{token}.mrpack"


def forget(core: AgentCore, token: str) -> None:
    READ.pop(token, None)
    if TOKEN_RE.match(token or ""):
        (upload_folder(core) / f"{token}.mrpack").unlink(missing_ok=True)


# ----------------------------------------------------------------------
# putting a pack's files in place
# ----------------------------------------------------------------------
async def _download_all(pack: Pack, folder: Path, job: JobHandle | None) -> list[dict[str, Any]]:
    wanted = pack.downloads
    total = sum(f.size or 0 for f in wanted)
    if job:
        job.step(
            f"Downloading {len(wanted)} file(s) from {pack.site}",
            total=total if total else None,
            unit="bytes",
        )
    fetched = []
    for item in wanted:
        assert item.url and (item.sha512 or item.sha1)
        target = check_archive_member(folder, item.path)
        spec = FileSpec(
            url=downloads.check_url(item.url),
            name=item.name,
            sha512=item.sha512,
            sha1=item.sha1,
            size=item.size,
            allow_unverified=False,
        )
        try:
            got = await downloads.download(spec, target.parent)
        except DownloadError as exc:
            raise ModpackError(f"{item.name}: {exc}") from exc
        # A CurseForge pack lists no SHA-512, so the known bad list is asked now.
        bad = item.sha1 and notes.known_bad(await asyncio.to_thread(got.path.read_bytes))
        if bad:
            got.path.unlink(missing_ok=True)
            raise ModpackError(f"{item.name} is on the list of known bad files ({bad['why']}).")
        fetched.append({"path": item.path, **got.to_dict()})
        if job and item.size:
            job.advance(item.size)
    return fetched


def _extract_overrides(pack: Pack, archive: Path, folder: Path, advance=None) -> list[str]:
    written: list[str] = []
    with zipfile.ZipFile(archive) as zf:
        for member, relative in _override_members(zf, pack.folders):
            top = relative.split("/", 1)[0]
            if top in CLIENT_ONLY or Path(relative).suffix.lower() in EXECUTABLE_EXTENSIONS:
                continue
            target = check_archive_member(folder, relative, zip_member_is_symlink(member))
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f".{target.name}.part")
            with zf.open(member) as source, open(temp, "wb") as out:
                shutil.copyfileobj(source, out, 1024 * 1024)
            temp.replace(target)
            written.append(relative)
            if advance:
                advance(1)
    return written


async def put_in_place(
    pack: Pack, archive: Path, folder: Path, job: JobHandle | None
) -> dict[str, Any]:
    """Download the pack's mods and extract its overrides into ``folder``."""
    fetched = await _download_all(pack, folder, job)
    if job:
        job.step("Copying the pack's other files", total=pack.overrides, unit="files")
    written = await asyncio.to_thread(
        _extract_overrides, pack, archive, folder, job.advance if job else None
    )
    return {"downloaded": fetched, "overrides": written}


def check_in_place(pack: Pack, folder: Path, result: dict[str, Any]) -> tuple[bool, str]:
    for item in pack.downloads:
        path = folder / item.path
        if not path.is_file():
            return False, f"{item.name} isn't there after the import"
        if item.size is not None and path.stat().st_size != item.size:
            return False, f"{item.name} isn't the size the pack lists"
    for relative in result["overrides"]:
        if not (folder / relative).is_file():
            return False, f"{relative} isn't there after the import"
    against = "CurseForge's sha1" if pack.site == "CurseForge" else "the pack's SHA-512"
    return True, (
        f"{len(pack.downloads)} mod file(s) checked against {against} and "
        f"{len(result['overrides'])} other file(s) in place"
    )


# ----------------------------------------------------------------------
# the two ways in
# ----------------------------------------------------------------------
async def import_new(
    core: AgentCore,
    token: str,
    *,
    name: str,
    directory: str,
    memory_mb: int | None = None,
    color: str | None = None,
    eula_accepted: bool = False,
    user: str = "system",
) -> dict[str, Any]:
    """Create a new server set up the way the pack says, with its files."""
    from .servertypes.create import create_server

    archive = upload_path(core, token)
    pack = await read(core, token, archive)
    if pack.problems:
        raise ModpackError(pack.problems[0])

    async def after_install(ctx: ServerContext, folder: Path, job: JobHandle | None) -> dict:
        result = await put_in_place(pack, archive, folder, job)
        ok, detail = check_in_place(pack, folder, result)
        if not ok:
            raise ModpackError(f"The import couldn't be checked: {detail}")
        return {"modpack": _summary(pack, result, detail)}

    result = await create_server(
        core,
        name=name,
        directory=directory,
        type_id=pack.type_id,
        minecraft=pack.minecraft,
        loader=pack.loader_version,
        memory_mb=memory_mb,
        color=color,
        eula_accepted=eula_accepted,
        user=user,
        after_install=after_install,
        title=f"Creating {name.strip()} from the {pack.name} modpack",
    )
    forget(core, token)
    ctx = core.get_server(result["server_id"])
    _list_needs(ctx, pack)
    await ctx.bus.publish(_event(pack, ctx.name, result.get("modpack") or {}))
    return result


def _matches(ctx: ServerContext, pack: Pack) -> bool:
    """Whether the server already runs the pack's type and versions, going
    only by what its console reported (or the version just installed and
    still pending). Unknown never counts as matching."""
    if ctx.config.server.type != pack.type_id:
        return False
    pending = ctx.server.pending_version or {}
    minecraft = ctx.server.mc_version or pending.get("minecraft_version")
    if minecraft != pack.minecraft:
        return False
    if not pack.loader_version:
        return True
    loader = ctx.server.loader_version or pending.get("loader_version")
    if not loader:
        return False
    # Forge's console prints only the build; the app records "<mc>-<build>".
    return loader == pack.loader_version or pack.loader_version.endswith(f"-{loader}")


def plan_into(ctx: ServerContext, pack: Pack) -> dict[str, Any]:
    """What importing into this server will do, for the confirmation."""
    return {
        "version_change": not _matches(ctx, pack),
        "current_type": ctx.config.server.type,
        "current_minecraft": ctx.server.mc_version,
        "content_folder": ctx.config.server_type.content_folder
        or servertypes.get(pack.type_id).content_folder,
        "running": ctx.server.running,
    }


async def import_into(ctx: ServerContext, token: str, user: str = "system") -> dict[str, Any]:
    """Import a pack into an existing server. Sets the server up on the
    pack's type and versions first when it isn't on them already."""
    from .safechange import SafeChange, before, run_safe_change
    from .servertypes.install import change_version

    archive = upload_path(ctx.core, token)
    pack = await read(ctx.core, token, archive)
    if pack.problems:
        raise ModpackError(pack.problems[0])
    version_result = None
    if not _matches(ctx, pack):
        version_result = await change_version(
            ctx, pack.type_id, pack.minecraft, pack.loader_version, user=user
        )
    folder = ctx.config.server_dir
    title = f"Importing the {pack.name} modpack into {ctx.name}"

    targets = await asyncio.to_thread(_pack_targets, pack, archive)

    async def change(job: JobHandle | None) -> dict[str, Any]:
        # Files the pack adds that weren't there before. The safety backup
        # puts back what was there; these are taken away again if the
        # import fails, so nothing of a half-done import is left loaded.
        new_files = [rel for rel in targets if not (folder / rel).exists()]
        try:
            moved = await asyncio.to_thread(_move_content_aside, ctx)
            result = await put_in_place(pack, archive, folder, job)
            ok, detail = await asyncio.to_thread(check_in_place, pack, folder, result)
            if not ok:
                raise ModpackError(f"The import couldn't be checked: {detail}")
        except BaseException:
            await asyncio.to_thread(_remove_new_files, folder, new_files)
            raise
        return {**result, "moved_aside": moved, "detail": detail}

    plan = SafeChange(
        title=title,
        change=change,
        check=None,  # the change checks itself, so it can tidy up first
        stop_server=True,
        take_backup=True,
        backup_name="pre-modpack",
        backup_note=before(title),
        # The usual backup list plus every top-level folder or file the
        # pack writes to (kubejs/, defaultconfigs/, scripts/ and the like),
        # so the undo covers everything the import changes.
        backup_includes=_backup_list(ctx, targets),
    )

    async def run(job: JobHandle | None) -> dict[str, Any]:
        return await run_safe_change(ctx.server, ctx.backups, plan, job=job, user=user)

    job, outcome = await ctx.core.jobs.run(
        "modpack", title, run, server_id=ctx.server_id, risky=True, user=user
    )
    forget(ctx.core, token)
    _list_needs(ctx, pack)
    summary = _summary(pack, outcome, outcome["detail"])
    ctx.core.db.audit(
        "modpack_import",
        user=user,
        target=ctx.server_id,
        detail=f"{pack.name} {pack.version or ''}".strip(),
        server_id=ctx.server_id,
    )
    await ctx.bus.publish(_event(pack, ctx.name, summary))
    return {
        "job_id": job.id,
        "modpack": summary,
        "moved_aside": outcome["moved_aside"],
        "version_change": version_result is not None,
        "undo": outcome.get("undo"),
    }


def _pack_targets(pack: Pack, archive: Path) -> list[str]:
    """Every path inside the server folder the import writes: the mods it
    downloads and the override files it extracts."""
    targets = [f.path.replace("\\", "/") for f in pack.downloads]
    with zipfile.ZipFile(archive) as zf:
        for _member, relative in _override_members(zf, pack.folders):
            top = relative.split("/", 1)[0]
            if top in CLIENT_ONLY or Path(relative).suffix.lower() in EXECUTABLE_EXTENSIONS:
                continue
            targets.append(relative)
    return list(dict.fromkeys(targets))


def _backup_list(ctx: ServerContext, targets: list[str]) -> list[str]:
    includes = list(ctx.config.backups.include)
    for extra in ctx.config.server_type.backup_extra:
        if extra not in includes:
            includes.append(extra)
    for relative in targets:
        top = relative.split("/", 1)[0]
        if top and top not in includes:
            includes.append(top)
    return includes


def _remove_new_files(folder: Path, relatives: list[str]) -> None:
    """Take away files a failed import added, and folders it left empty."""
    for relative in relatives:
        try:
            path = check_archive_member(folder, relative)
        except PathSafetyError:
            continue
        if path.is_file() and not path.is_symlink():
            path.unlink(missing_ok=True)
        parent = path.parent
        while parent != folder and is_inside(folder, parent):
            try:
                parent.rmdir()  # only when empty
            except OSError:
                break
            parent = parent.parent


def _move_content_aside(ctx: ServerContext) -> dict[str, Any]:
    """Move the add-ons the server had into the trash folder (kept, never
    deleted), so only the pack's own are loaded."""
    source = ctx.config.mods_dir
    if not source.is_dir():
        return {"moved": [], "folder": None}
    trash = ctx.config.mod_trash_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-before-modpack"
    moved = []
    for entry in sorted(source.iterdir()):
        if not entry.is_file() or entry.is_symlink() or ".jar" not in entry.name:
            continue
        trash.mkdir(parents=True, exist_ok=True)
        shutil.move(str(entry), str(trash / entry.name))
        moved.append(entry.name)
    return {"moved": moved, "folder": str(trash) if moved else None}


def _summary(pack: Pack, result: dict[str, Any], detail: str) -> dict[str, Any]:
    return {
        "name": pack.name,
        "version": pack.version,
        "minecraft_version": pack.minecraft,
        "type": pack.type_id,
        "loader_version": pack.loader_version,
        "downloaded": len(result.get("downloaded") or []),
        "overrides": len(result.get("overrides") or []),
        "client_only": [f.name for f in pack.files if f.status == CLIENT],
        "needs_you": [f.need["name"] for f in pack.files if f.need],
        "check": detail,
    }


def _list_needs(ctx: ServerContext, pack: Pack) -> None:
    """The pack's mods whose authors keep them from other apps, under the
    server's "Needs you", to be dropped in by hand."""
    for item in pack.files:
        if item.need:
            ctx.mods.need_you(item.need)


async def from_curseforge(core: AgentCore, project: str) -> tuple[str, Pack]:
    """Download a CurseForge modpack's newest release (sha1 checked) as an
    upload, then read it like one."""
    from .mods.curseforge import MODPACKS

    client = CurseForgeClient(core.config)
    try:
        version = await client.latest_for(project, None, kind=MODPACKS)
        if not version:
            raise ModpackError("CurseForge has no file of that modpack to download.")
        token, path = new_upload(core)
        spec = await client.spec(version, ext=".zip", name=path.name)
    except Blocked as exc:
        raise ModpackError(str(exc)) from exc
    except SourceError as exc:
        raise ModpackError(str(exc)) from exc
    try:
        await downloads.download(replace(spec, max_bytes=MAX_PACK_BYTES), path.parent)
        return token, await read(core, token, path)
    except BaseException as exc:
        forget(core, token)
        if isinstance(exc, DownloadError):
            raise ModpackError(str(exc)) from exc
        raise


def _event(pack: Pack, server_name: str, summary: dict[str, Any]) -> Event:
    return Event(
        type="modpack_imported",
        level="success",
        message=(
            f"Imported the {pack.name} modpack into {server_name}: "
            f"{summary.get('downloaded', 0)} mod file(s), checked"
        ),
        data=summary,
    )
