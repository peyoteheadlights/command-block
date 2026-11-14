"""Server / Players / Both: who needs each mod.

Every mod on the Mods page is marked:

  * **Server**: only the server needs it (players can join without it)
  * **Players**: only players need it (a minimap, shaders, a resource pack).
    The server must not load it, so it lives in the player-only folder
    (``FOLDER`` in the server folder, never inside mods/). Plain Minecraft
    and the loaders only load mods/ and plugins/, so nothing there runs.
  * **Both**: the server and every player need it

Where a mark comes from, first match wins, and the page says which one
(``side_from``), so a mark is never a guess shown as a fact:

  1. ``you``: the owner changed it
  2. ``modpack``: the linked modpack's own ``env`` for that file
  3. ``modrinth`` / ``curseforge``: the site's own sides for the project
  4. ``file``: the jar says it (Fabric and Quilt ``environment``; a plugin
     only ever runs on the server)
  5. ``unknown``: nothing says. Shown as Both, with "not sure"

Changing a mark to Players moves the jar out of mods/ into the player-only
folder; changing it back moves it in again. Both need the server stopped.
Server and Both only change the saved mark. A file is moved, never
deleted, so a mistake is one more change away.

Your marks are kept in a small file in the server folder (``MARKS_FILE``),
so backups, Undo and copies of the server carry them. It is read strictly:
anything odd is left out. A mark is kept by mod id (so it survives an
update), or by mod id and file name when two jars share an id.

The player-only folder is locked from the server's own Windows user when
that is on (``lock``), and the agent keeps each file's hash (``RECORD``, in
its own database the server can't reach), so a file changed outside the
app is pointed out. Two options per server: back up the folder or not, and
leave out files Modrinth lists (they are downloaded again by hash after a
restore, with "Get it again").

The odd-setup warnings read each jar's own "depends": a mod the server
loads that needs one only players have, or a mod players get that needs
one marked Server only.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import downloads, serveruser
from ..minecraft.properties import FormError
from ..security.paths import assert_not_symlink, is_inside, safe_filename, safe_join
from .addfrom import known_source
from .jarinfo import DISABLED_SUFFIX, ModInfo, read_mod_jar, sha256_file, wrong_loader_reason
from .manager import MAX_FILE_BYTES, UNKNOWN_FILE, ModError, refuse_known_bad
from .modrinth import ModrinthError

if TYPE_CHECKING:
    from ..core import ServerContext
    from .manager import ModManager

log = logging.getLogger("msc.mods.sides")

FOLDER = "player-only"
MARKS_FILE = "command-block-sides.json"
RECORD = "player_only_files"  # name -> sha256, sha512, site, size, mtime
BACKUP = "player_only_backup"  # False: leave the folder out of backups
SKIP_LISTED = "player_only_skip_listed"  # True: leave out files Modrinth lists
SIDES = ("server", "players", "both")
PLUGIN_LOADERS = {"paper", "bukkit"}
MOD_FILES = (
    "fabric.mod.json",
    "quilt.mod.json",
    "META-INF/mods.toml",
    "META-INF/neoforge.mods.toml",
)
FILE_EXT = {".jar", ".zip"}
WORDS = {"server": "Server", "players": "Players", "both": "Both"}


class SideError(ModError):
    """A mark that can't be changed, in plain words."""


# ---------------------------------------------------------------- the marks
def _keys(mod: ModInfo) -> tuple[str, str]:
    """(by id and file name, by id). The file name without ".disabled"."""
    plain = mod.filename.removesuffix(DISABLED_SUFFIX)
    return f"{mod.mod_id}:{plain}", mod.mod_id or plain


def read_marks(config: Any) -> dict[str, str]:
    """The owner's marks. The file sits in the server folder, so it is read
    strictly: a key that isn't text or a value that isn't a side is left out."""
    try:
        data = json.loads((config.server_dir / MARKS_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and len(k) <= 300 and v in SIDES}


def _save_mark(mods: ModManager, mod: ModInfo, side: str | None) -> None:
    marks = read_marks(mods.config)
    exact, by_id = _keys(mod)
    marks.pop(exact, None)
    marks.pop(by_id, None)
    if side:
        twins = [m for m in mods.scan() if m.mod_id == mod.mod_id and m.filename != mod.filename]
        marks[exact if mod.mod_id and twins else by_id] = side
    path = mods.config.server_dir / MARKS_FILE
    temp = path.with_name(f".{MARKS_FILE}.part")
    temp.write_text(json.dumps(marks, indent=1, sort_keys=True), "utf-8")
    temp.replace(path)


def from_site(record: dict[str, Any]) -> str | None:
    """A mark from the sides a site gave (saved in the mod's record)."""
    if record.get("client_only"):  # CurseForge: tagged for the game, not servers
        return "players"
    client, server = record.get("client_side"), record.get("server_side")
    if not client or not server:
        return None
    if server == "unsupported":
        return "players"
    if client == "unsupported":
        return "server"
    if server == "required" and client == "optional":
        return "server"
    if client == "required" and server == "optional":
        return "players"
    return "both"


def from_pack(env: dict[str, Any]) -> str | None:
    """A mark from a modpack file's ``env`` (client / server: required,
    optional or unsupported)."""
    return from_site({"client_side": env.get("client"), "server_side": env.get("server")})


def from_file(mod: ModInfo) -> str | None:
    if mod.loader in PLUGIN_LOADERS:
        return "server"
    if mod.loader not in ("fabric", "quilt"):
        return None  # Forge and NeoForge jars don't say
    return {"client": "players", "server": "server"}.get(mod.environment, "both")


def mark(
    mods: ModManager,
    mod: ModInfo,
    pack_sides: dict[str, str] | None = None,
    marks: dict[str, str] | None = None,
) -> tuple[str, str]:
    """(side, where it came from) for a jar in mods/ or plugins/."""
    if mod.loader in PLUGIN_LOADERS:
        return "server", "file"  # plugins only ever run on the server
    marks = read_marks(mods.config) if marks is None else marks
    exact, by_id = _keys(mod)
    chosen = marks.get(exact) or marks.get(by_id)
    if chosen:
        return chosen, "you"
    if pack_sides and mod.sha256 in pack_sides:
        return pack_sides[mod.sha256], "modpack"
    record = mods.record_of(mod)
    site = from_site(record)
    if site:
        return site, mods.source_of(mod) or "modrinth"
    found = from_file(mod)
    if found:
        return found, "file"
    return "both", "unknown"


def pack_sides(ctx: ServerContext) -> dict[str, str]:
    """sha256 -> side, from the linked modpack's files (agent/linkedpack.py)."""
    from .. import linkedpack

    link = linkedpack.read_link(ctx) or {}
    return {
        entry["sha256"]: entry["side"]
        for entry in link.get("files") or []
        if entry.get("side") in SIDES
    }


def add_marks(ctx: ServerContext, rows: list[dict[str, Any]]) -> None:
    """Put ``side`` and ``side_from`` on the Mods page's rows."""
    by_file = {m.filename: m for m in ctx.mods.scan(use_cache=True)}
    from_pack_ = pack_sides(ctx)
    marks = read_marks(ctx.config)
    for row in rows:
        mod = by_file.get(row["filename"])
        if mod:
            row["side"], row["side_from"] = mark(ctx.mods, mod, from_pack_, marks)


async def catch_up(mods: ModManager) -> None:
    """Ask Modrinth once for the sides of Modrinth mods that don't have
    them saved yet (all in one call). Nothing is guessed if it can't
    answer: the mark falls back to the file."""
    missing: dict[str, ModInfo] = {}
    for mod in mods.scan(use_cache=True):
        if mod.loader in PLUGIN_LOADERS or mods.source_of(mod) != "modrinth":
            continue
        record = mods.record_of(mod)
        project = record.get("modrinth_project") or record.get("project")
        if project and "client_side" not in record:
            missing[str(project)] = mod
    if not missing:
        return
    try:
        found = await mods.modrinth.projects(list(missing))
    except ModrinthError:
        log.info("couldn't ask Modrinth which side mods are for", exc_info=True)
        return
    for project, mod in missing.items():
        about = found.get(project)
        if not about:
            continue
        record = mods.record_of(mod)
        record["client_side"] = about.get("client_side")
        record["server_side"] = about.get("server_side")
        mods.db.set_setting(f"mod_source:{mod.mod_id}", record)


# ---------------------------------------------------------------- the player-only folder
def folder(ctx: ServerContext) -> Path:
    if not ctx.config.server_dir_configured:
        raise ModError("The Minecraft server folder isn't set.")
    return ctx.config.server_dir / FOLDER


def _kind(data: bytes) -> str | None:
    """mod, resource pack or shader pack, read from the zip's own files."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return None
    if any(n in names for n in MOD_FILES):
        return "mod"
    if "pack.mcmeta" in names:
        return "resource pack"
    # A shader pack has a shaders/ folder, at the top or in one folder.
    if any("shaders" in n.split("/")[:2] for n in names):
        return "shader pack"
    return None


def lock(config: Any) -> None:
    """Keep the server's own Windows user out of the player-only folder
    (agent/serveruser.py). Done when the folder is made and before each
    server start, so turning the separate user on later is covered too."""
    where = config.server_dir / FOLDER
    if serveruser.is_on(config) and where.is_dir():
        serveruser.deny_all(where)


def _note(ctx: ServerContext, path: Path, site: str | None, sha512: str | None = None) -> None:
    """Remember a player-only file's hashes, in the agent's own database."""
    record = ctx.mods.db.get_setting(RECORD) or {}
    if sha512 is None:
        sha512 = hashlib.sha512(path.read_bytes()).hexdigest()
    stat = path.stat()
    record[path.name] = {
        "sha256": sha256_file(path),
        "sha512": sha512,
        "site": site,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }
    ctx.mods.db.set_setting(RECORD, record)


def _forget(ctx: ServerContext, name: str) -> None:
    record = ctx.mods.db.get_setting(RECORD) or {}
    if record.pop(name, None) is not None:
        ctx.mods.db.set_setting(RECORD, record)


def _changed(ctx: ServerContext, path: Path, known: dict[str, Any] | None) -> bool:
    """True when a file isn't the one the app first saw. Hashed again only
    when its size or time changed, so opening the page stays light."""
    if not known:
        # First seen here (a copied server, or put in by hand): taken as
        # it is now. The server's own user can't write here (``lock``).
        _note(ctx, path, None)
        return False
    stat = path.stat()
    if stat.st_size == known.get("size") and stat.st_mtime == known.get("mtime"):
        return False
    if sha256_file(path) != known.get("sha256"):
        return True
    _note(ctx, path, known.get("site"), known.get("sha512"))  # only touched
    return False


def options(ctx: ServerContext) -> dict[str, bool]:
    db = ctx.mods.db
    return {
        "backup": db.get_setting(BACKUP) is not False,
        "skip_listed": db.get_setting(SKIP_LISTED) is True,
    }


def set_options(ctx: ServerContext, backup: bool, skip_listed: bool) -> dict[str, bool]:
    ctx.mods.db.set_setting(BACKUP, backup)
    ctx.mods.db.set_setting(SKIP_LISTED, skip_listed)
    return options(ctx)


def backup_names(db: Any) -> list[str]:
    """What a backup takes from the server folder for this (agent/backups)."""
    return [MARKS_FILE] + ([FOLDER] if db.get_setting(BACKUP) is not False else [])


def backup_skips(config: Any, db: Any) -> set[Path]:
    """Player-only files a backup leaves out: the ones Modrinth lists, when
    the owner chose that. "Get it again" downloads them by hash."""
    if db.get_setting(SKIP_LISTED) is not True:
        return set()
    record = db.get_setting(RECORD) or {}
    return {
        config.server_dir / FOLDER / name
        for name, known in record.items()
        if isinstance(known, dict) and known.get("site") == "modrinth"
    }


def player_files(ctx: ServerContext) -> list[dict[str, Any]]:
    """What's in the player-only folder, for the Mods page: each file, a
    file changed outside the app, and files the app knows that are gone
    (after a restore that left out the ones Modrinth lists)."""
    where = ctx.config.server_dir / FOLDER if ctx.config.server_dir_configured else None
    record = ctx.mods.db.get_setting(RECORD) or {}
    found = []
    seen = set()
    for entry in sorted(where.iterdir()) if where and where.is_dir() else []:
        if not entry.is_file() or entry.is_symlink() or entry.suffix.lower() not in FILE_EXT:
            continue
        seen.add(entry.name)
        known = record.get(entry.name)
        row: dict[str, Any] = {
            "filename": entry.name,
            "size_bytes": entry.stat().st_size,
            "kind": "mod" if entry.suffix.lower() == ".jar" else "pack",
            "name": entry.stem,
            "version": "",
            "changed": _changed(ctx, entry, known),
            "missing": False,
            "site": (known or {}).get("site"),
        }
        if row["kind"] == "mod":
            info = read_mod_jar(entry)
            row.update(name=info.name or info.mod_id or entry.stem, version=info.version)
            row["mod_id"] = info.mod_id
        found.append(row)
    for name, known in sorted(record.items()):
        if name not in seen and isinstance(known, dict):
            found.append(
                {
                    "filename": name,
                    "size_bytes": known.get("size") or 0,
                    "kind": "mod" if name.lower().endswith(".jar") else "pack",
                    "name": Path(name).stem,
                    "version": "",
                    "changed": False,
                    "missing": True,
                    "site": known.get("site"),
                }
            )
    return found


async def fetch_again(ctx: ServerContext, filename: str, user: str) -> dict[str, Any]:
    """Get a missing player-only file again from Modrinth: the exact file,
    found and checked by the SHA-512 saved when it was added."""
    mods = ctx.mods
    where = folder(ctx)
    target = safe_join(where, filename, allowed_extensions=FILE_EXT)
    known = (mods.db.get_setting(RECORD) or {}).get(target.name)
    if not isinstance(known, dict) or target.exists():
        raise ModError(f"{filename} isn't missing.")
    if known.get("site") != "modrinth" or not known.get("sha512"):
        raise ModError(f"{filename} isn't on Modrinth, so it can't be downloaded again.")
    url = await mods.modrinth.file_url(known["sha512"])
    if not url:
        raise ModError(f"Modrinth doesn't have {filename} any more.")
    data, _ = await downloads.download_bytes(
        downloads.FileSpec(
            url=url, name=target.name, sha512=known["sha512"], allow_unverified=False
        )
    )
    where.mkdir(parents=True, exist_ok=True)
    lock(ctx.config)
    temp = where / f".{target.name}.part"
    temp.write_bytes(data)
    temp.replace(target)
    _note(ctx, target, "modrinth", known["sha512"])
    mods.record("players_add", user, mod_name=target.name, source=url, detail="Downloaded again")
    return {"added": target.name, "kind": "mod" if target.suffix == ".jar" else "pack"}


def _player_mods(ctx: ServerContext) -> list[ModInfo]:
    where = ctx.config.server_dir / FOLDER if ctx.config.server_dir_configured else None
    if not where or not where.is_dir():
        return []
    return [
        read_mod_jar(e) for e in sorted(where.glob("*.jar")) if e.is_file() and not e.is_symlink()
    ]


async def add(
    ctx: ServerContext, filename: str, data: bytes, user: str, confirm: bool = False
) -> dict[str, Any]:
    """Put a mod, resource pack or shader pack in the player-only folder.
    The server never loads it; it's there for the modpack."""
    mods = ctx.mods
    safe_filename(filename, FILE_EXT)
    if len(data) > MAX_FILE_BYTES:
        raise ModError("That file is bigger than the 300 MB limit.")
    if not data.startswith(b"PK"):
        raise ModError("That file isn't a .jar or .zip file.")
    refuse_known_bad(data, filename)
    kind = _kind(data)
    jar = filename.lower().endswith(".jar")
    if kind is None or (kind == "mod") != jar:
        raise ModError(
            f"{filename} isn't a mod (.jar), a resource pack or a shader pack (.zip), "
            "so it wasn't added."
        )
    where = folder(ctx)
    where.mkdir(parents=True, exist_ok=True)
    lock(ctx.config)
    target = safe_join(where, filename, allowed_extensions=FILE_EXT)
    if target.exists():
        raise ModError(f"{filename} is already there, so nothing was changed.")
    temp = where / f".{filename}.part"
    temp.write_bytes(data)
    try:
        if jar:
            info = read_mod_jar(temp)
            if info.loader in PLUGIN_LOADERS:
                raise ModError(f"{filename} is a plugin. Plugins only run on the server.")
            if mods.server_type.content == "mods":
                reason = wrong_loader_reason(info, mods.server_type.accepts, mods.server_type.name)
                if reason:
                    raise ModError(reason + " It wasn't added.")
        listed = await known_source(mods, data)
        if not confirm and not listed:
            raise FormError({"confirm": UNKNOWN_FILE})
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    _note(ctx, target, listed[0] if listed else None, hashlib.sha512(data).hexdigest())
    mods.record("players_add", user, mod_name=filename, source="upload", detail=f"Added {filename}")
    return {"added": filename, "kind": kind}


async def remove(ctx: ServerContext, filename: str, user: str) -> dict[str, Any]:
    """Move a player-only file to the mods trash (never deleted)."""
    mods = ctx.mods
    path = safe_join(folder(ctx), filename, allowed_extensions=FILE_EXT)
    if not path.is_file():
        raise ModError(f"{filename} isn't in the player-only folder.")
    assert_not_symlink(path)
    trash = mods.trash_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{path.name}"
    shutil.move(str(path), str(trash))
    _forget(ctx, path.name)
    mods.record("players_remove", user, mod_name=filename, detail=f"Moved to {trash.name}")
    return {"removed": filename}


# ---------------------------------------------------------------- changing a mark
async def set_side(ctx: ServerContext, filename: str, side: str, user: str) -> dict[str, Any]:
    """Change who needs a mod. To Players moves it out of mods/ (the
    server stops loading it); from Players moves it back in."""
    mods = ctx.mods
    if side not in SIDES:
        raise SideError("Pick Server, Players or Both.")
    name = safe_filename(filename)
    player_path = folder(ctx) / name
    if player_path.is_file() and is_inside(folder(ctx), player_path):
        if side == "players":
            return {"filename": name, "side": side, "moved": False}
        return await _bring_in(ctx, player_path, side, user)

    mod = mods.find(name)
    if mod.loader in PLUGIN_LOADERS:
        raise SideError(f"{mod.name} is a plugin. Plugins only run on the server.")
    if side != "players":
        _save_mark(mods, mod, side)
        mods.record("side", user, mod, detail=f"Marked {WORDS[side]}")
        return {"filename": name, "side": side, "moved": False}

    mods.require_server_offline(f"move a {mods.word} to Players")
    path = Path(mod.path)
    assert_not_symlink(path)
    if not is_inside(mods.mods_dir, path):
        raise ModError(f"That file isn't inside the {mods.server_type.content} folder.")
    where = folder(ctx)
    where.mkdir(parents=True, exist_ok=True)
    lock(ctx.config)
    plain = path.name.removesuffix(DISABLED_SUFFIX)
    target = safe_join(where, plain, allowed_extensions={".jar"})
    if target.exists():
        raise ModError(f"{plain} is already in the player-only folder.")
    path.rename(target)
    _save_mark(mods, mod, None)
    source = mods.source_of(mod)
    _note(ctx, target, source if source in ("modrinth", "curseforge") else None)
    mods._cache = None
    mods.record("side", user, mod, detail=f"Moved {plain} to Players only")
    return {"filename": plain, "side": side, "moved": True}


async def _bring_in(ctx: ServerContext, path: Path, side: str, user: str) -> dict[str, Any]:
    mods = ctx.mods
    if path.suffix.lower() != ".jar":
        raise SideError(f"{path.name} is a pack for players. The server can't load it.")
    if mods.server_type.content != "mods":
        raise SideError(
            f"{mods.server_type.name} servers load plugins, not mods, so {path.name} stays "
            "with Players."
        )
    mods.require_server_offline("move a mod onto the server")
    assert_not_symlink(path)
    info = read_mod_jar(path)
    mods._check_loader(info, discarded=False)
    target = safe_join(mods.mods_dir, path.name, allowed_extensions={".jar"})
    if target.exists():
        raise ModError(f"{path.name} is already in the {mods.server_type.content} folder.")
    path.rename(target)
    _forget(ctx, path.name)
    mods._cache = None
    _save_mark(mods, read_mod_jar(target), side)
    mods.record("side", user, info, detail=f"Moved {path.name} to {WORDS[side]}")
    return {"filename": path.name, "side": side, "moved": True}


# ---------------------------------------------------------------- odd setups
def odd_setups(ctx: ServerContext, rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Mods whose needs sit on the wrong side, from each jar's own
    "depends". Only mods the app can see are named; Minecraft, the loader
    and Java are never "missing" here."""
    mods = ctx.mods
    side_of = {r["filename"]: r.get("side") for r in rows}
    on_server = [m for m in mods.scan(use_cache=True) if m.enabled]
    for_players = _player_mods(ctx)

    def ids(found: list[ModInfo]) -> dict[str, ModInfo]:
        out: dict[str, ModInfo] = {}
        for m in found:
            for one in (m, *m.bundled):
                if one.mod_id:
                    out.setdefault(one.mod_id, m)
        return out

    server_ids, player_ids = ids(on_server), ids(for_players)
    players_get = ids([m for m in on_server if side_of.get(m.filename) != "server"] + for_players)
    warnings = []
    for mod in on_server:
        for dep in mod.dependencies:
            if dep.kind == "depends" and dep.mod_id not in server_ids and dep.mod_id in player_ids:
                needed = player_ids[dep.mod_id]
                warnings.append(
                    {
                        "filename": mod.filename,
                        "text": f"{mod.name} runs on the server and needs "
                        f"{needed.name or needed.filename}, which is marked Players. Mark it "
                        "Both, or the server may not start.",
                    }
                )
    givers = [m for m in on_server if side_of.get(m.filename) == "both"] + for_players
    for mod in givers:
        for dep in mod.dependencies:
            wanted = server_ids.get(dep.mod_id)
            if (
                dep.kind == "depends"
                and wanted
                and dep.mod_id not in players_get
                and side_of.get(wanted.filename) == "server"
            ):
                warnings.append(
                    {
                        "filename": mod.filename,
                        "text": f"{mod.name} is for players and needs "
                        f"{wanted.name or wanted.filename}, which is marked Server. Players "
                        "won't get it in the modpack. Mark it Both.",
                    }
                )
    return warnings
