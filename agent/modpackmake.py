"""Making a modpack from a server, and sharing it.

A server's setup goes into one ``.mrpack`` (the format Modrinth, Prism
Launcher and our own "New server from a modpack" read):

  * each enabled mod or plugin, and each file in the player-only folder
    (agent/mods/sides.py), with its Server / Players / Both mark as the
    entry's ``env``
  * a file Modrinth has goes in as a download entry (Modrinth's own link
    and the file's hashes). One CurseForge has goes in the same way, but
    only when its author lets other apps download it. When they don't, it
    is never put in: it is listed as "get this one yourself"
  * any other file is an own file. A jar or pack goes in ``overrides/``
    (Both), ``server-overrides/`` (Server) or ``client-overrides/``
    (Players), and the modpack gets the trust note. Plugins that aren't on
    Modrinth are listed instead (our importer and launchers only download
    from a few sites)
  * the config files the person ticked, in ``overrides/``
  * a plugin server's modpack also carries ``server-overrides/
    command-block.json`` (agent/modpack.py reads it), since a .mrpack can
    only name Fabric, Quilt, Forge or NeoForge. The players' loader is
    picked (Fabric by default)

It also writes the Companion's settings from it (agent/companion.py).

"Leave out server-only mods" drops every Server entry, every
``server-overrides/`` file and command-block.json, so players get a
smaller file.

It runs as a job. The zip is written straight to disk (jars stored as they
are, not squeezed again: weak PCs), never held in memory. Only the newest
modpack of each server is kept, in the agent's data folder; it can be
saved by the apps (the phones hand it to the share menu) or posted by the
Discord bot in the server's chat channels when it fits Discord's limit.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from . import companion, titlescreen
from .discord.client import DiscordError
from .modpack import INDEX, MAX_PACK_BYTES, SERVER_NOTE, ModpackError
from .mods import sides
from .mods.curseforge import Blocked, file_prints
from .mods.sources import LABELS, SourceError
from .security.paths import EXECUTABLE_EXTENSIONS, is_inside, safe_filename

if TYPE_CHECKING:
    from .core import ServerContext
    from .jobs import JobHandle

log = logging.getLogger("msc.modpackmake")

FOLDER = "modpack-exports"
MADE = "modpack_made"  # the newest modpack: token, filename, name, version...
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
NAME_RE = re.compile(r"^[^\x00-\x1f\\/:*?\"<>|]{1,64}$")
VERSION_RE = re.compile(r"^[\w.+-]{1,32}$")
CONFIG_FOLDERS = ("config", "defaultconfigs")
MAX_CONFIGS = 2000
MAX_CONFIG_BYTES = 5 * 1024 * 1024
DISCORD_LIMIT = 10 * 1024 * 1024  # what a bot may post where nobody boosted
PLAYERS_LOADERS = {"fabric": "Fabric", "quilt": "Quilt"}
ENV = {
    "server": {"client": "unsupported", "server": "required"},
    "players": {"client": "required", "server": "unsupported"},
    "both": {"client": "required", "server": "required"},
}
SIDE_FOLDER = {"both": "overrides", "server": "server-overrides", "players": "client-overrides"}
PACK_FOLDER = {"resource pack": "resourcepacks", "shader pack": "shaderpacks"}
SITE_HOSTS = {"cdn.modrinth.com", "edge.forgecdn.net", "mediafilez.forgecdn.net"}
TRUST_NOTE = (
    "This modpack has files that aren't from Modrinth or CurseForge. Only share it with "
    "people you trust."
)
MODRINTH_DOWN = "Modrinth didn't answer, so the modpack wasn't made. Try again in a minute."
CURSEFORGE_DOWN = (
    "CurseForge didn't answer, so the app can't tell which of your mods it may put in the "
    "modpack. Try again in a minute."
)


@dataclass
class Item:
    """One file going into the modpack (or listed beside it)."""

    source: Path
    path: str  # where it lands: mods/x.jar, plugins/x.jar, resourcepacks/x.zip
    side: str  # server, players, both
    name: str
    plugin: bool = False
    site: str | None = None  # a plugin's site, for command-block.json
    sha1: str = ""
    sha512: str = ""
    sha256: str = ""  # for the Companion's check (agent/companion.py)
    size: int = 0
    url: str | None = None  # a site's own download link
    need: dict[str, Any] | None = None  # CurseForge: its author says no


# ---------------------------------------------------------------- what can go in
def _server_dir(ctx: ServerContext) -> Path:
    if not ctx.config.server_dir_configured:
        raise ModpackError("The Minecraft server folder isn't set.")
    return ctx.config.server_dir


def config_files(ctx: ServerContext) -> list[dict[str, Any]]:
    """The config files that can be ticked: config/ and defaultconfigs/,
    no links, no programs or jars, none bigger than 5 MB."""
    base = _server_dir(ctx)
    found: list[dict[str, Any]] = []
    for top in CONFIG_FOLDERS:
        start = base / top
        if not start.is_dir() or start.is_symlink():
            continue
        for root, dirs, names in os.walk(start, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(root, d)))
            for name in sorted(names):
                path = Path(root) / name
                suffix = path.suffix.lower()
                if path.is_symlink() or suffix in EXECUTABLE_EXTENSIONS or suffix == ".jar":
                    continue
                if path.relative_to(base).as_posix() == companion.MODS_FILE:
                    continue  # the server's own check settings, not for players
                size = path.stat().st_size
                if size <= MAX_CONFIG_BYTES:
                    found.append({"path": path.relative_to(base).as_posix(), "size_bytes": size})
                if len(found) >= MAX_CONFIGS:
                    return found
    return found


def _next_version(last: str | None) -> str:
    match = re.match(r"^(.*?)(\d+)$", last or "")
    return f"{match.group(1)}{int(match.group(2)) + 1}" if match else "1"


def made(ctx: ServerContext) -> dict[str, Any] | None:
    """The newest modpack made from this server, while its file is there."""
    last = ctx.db.get_setting(MADE)
    if not isinstance(last, dict) or not TOKEN_RE.match(str(last.get("token") or "")):
        return None
    return last if _file(ctx, last["token"]).is_file() else None


def _chat_channels(ctx: ServerContext) -> list[str]:
    bot = getattr(ctx.core, "discord", None)
    if bot is None or not bot.connected:
        return []
    return [
        r["channel_id"] for r in bot.chat_channels(bot.guilds) if r["server_id"] == ctx.server_id
    ]


def options(ctx: ServerContext) -> dict[str, Any]:
    """What the Make a modpack screen offers."""
    server_type = ctx.mods.server_type
    if server_type.edition == "bedrock":
        raise ModpackError("Bedrock servers can't make a modpack. Modpacks are for Java.")
    last = ctx.db.get_setting(MADE)
    last = last if isinstance(last, dict) else {}
    return {
        "name": last.get("name") or ctx.name,
        "version": _next_version(last.get("version")),
        "configs": config_files(ctx),
        # A plugin or plain server: players' mods need a loader picked.
        "players_loaders": []
        if server_type.content == "mods"
        else [{"id": k, "name": v} for k, v in PLAYERS_LOADERS.items()],
        "discord": bool(_chat_channels(ctx)),
        "discord_limit_bytes": DISCORD_LIMIT,
        # Needs the Companion mod on the server, which does the join check.
        "required_to_join": {
            "available": companion.on_server(ctx.mods),
            "on": bool(last.get("required")),
        },
        "chat_line": last.get("chat_line") or {"on": True, "text": companion.CHAT_LINE_DEFAULT},
        "title_screen": titlescreen.options(ctx, last, _folder(ctx)),
        "made": made(ctx),
    }


def _items(ctx: ServerContext) -> list[Item]:
    mods = ctx.mods
    items: list[Item] = []
    if mods.server_type.content in ("mods", "plugins"):
        from_pack, marks = sides.pack_sides(ctx), sides.read_marks(ctx.config)
        folder = mods.server_type.content_folder or mods.server_type.content
        for mod in mods.scan():
            path = Path(mod.path)
            if not mod.enabled or path.is_symlink() or not path.is_file():
                continue
            plugin = mods.server_type.content == "plugins" or mod.loader in sides.PLUGIN_LOADERS
            side = "server" if plugin else sides.mark(mods, mod, from_pack, marks)[0]
            if mod.mod_id == companion.MOD_ID:
                side = "both"  # players need it too, or the join check can't ask them
            source = mods.source_of(mod)
            items.append(
                Item(
                    source=path,
                    path=f"{folder}/{path.name}",
                    side=side,
                    name=mod.name or mod.mod_id or path.stem,
                    plugin=plugin,
                    site=LABELS.get(source or "", "a file") if plugin else None,
                )
            )
    where = _server_dir(ctx) / sides.FOLDER
    for entry in sorted(where.iterdir()) if where.is_dir() else []:
        suffix = entry.suffix.lower()
        if entry.is_symlink() or not entry.is_file() or suffix not in sides.FILE_EXT:
            continue
        if suffix == ".jar":
            inside = f"mods/{entry.name}"
        else:
            kind = sides._kind(entry)
            if kind not in PACK_FOLDER:
                continue
            inside = f"{PACK_FOLDER[kind]}/{entry.name}"
        items.append(Item(source=entry, path=inside, side="players", name=entry.stem))
    seen: dict[str, Item] = {}
    for item in items:
        if item.path.lower() in seen:
            raise ModpackError(
                f"Two files are named {Path(item.path).name} (one on the server, one in Players "
                "only). Rename or remove one, then make the modpack again."
            )
        seen[item.path.lower()] = item
    return items


def _hash(item: Item) -> None:
    sha1, sha512, sha256 = hashlib.sha1(), hashlib.sha512(), hashlib.sha256()
    with item.source.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            sha1.update(chunk)
            sha512.update(chunk)
            sha256.update(chunk)
    item.sha1, item.sha512 = sha1.hexdigest(), sha512.hexdigest()
    item.sha256 = sha256.hexdigest()
    item.size = item.source.stat().st_size


def _site_link(url: Any) -> str | None:
    parsed = urlparse(str(url or ""))
    return str(url) if parsed.scheme == "https" and parsed.hostname in SITE_HOSTS else None


async def _look_up(ctx: ServerContext, items: list[Item]) -> None:
    """Which files Modrinth has (one call for all), then which of the rest
    CurseForge has and lets other apps download. A match counts only when
    the site's file has the same hashes as ours."""
    mods = ctx.mods
    if not items:
        return
    try:
        found = await mods.modrinth.installed_versions([i.sha1 for i in items])
    except SourceError as exc:
        raise ModpackError(MODRINTH_DOWN) from exc
    for item in items:
        file = (found.get(item.sha1) or {}).get("file") or {}
        if file.get("sha1") == item.sha1 and str(file.get("sha512") or "").lower() == item.sha512:
            item.url = _site_link(file.get("url"))
    # Plugins not on Modrinth are listed by name, never looked up further.
    rest = [i for i in items if not i.url and not i.plugin]
    if not rest:
        return
    prints = await asyncio.to_thread(lambda: [file_prints(i.source)[0] for i in rest])
    try:
        matches = await mods.curseforge.by_fingerprint(prints)
    except SourceError as exc:
        raise ModpackError(CURSEFORGE_DOWN) from exc
    for item, print_ in zip(rest, prints, strict=True):
        version = matches.get(print_)
        if not version or (version.get("file") or {}).get("sha1") != item.sha1:
            continue
        try:
            spec = await mods.curseforge.spec(version, ext=item.source.suffix.lower())
        except Blocked as exc:
            item.need = {**exc.need, "name": exc.need.get("name") or item.name}
            continue
        except SourceError:
            # CurseForge lists it but offers no checked link: never put in as our own.
            item.need = {"name": item.name, "filename": item.source.name, "page": None}
            continue
        item.url = _site_link(spec.url)


async def _players_loader(ctx: ServerContext, loader: str, minecraft: str) -> dict[str, str]:
    from .servertypes import versions

    if loader not in PLAYERS_LOADERS:
        raise ModpackError("Pick Fabric or Quilt for the players' mods.")
    try:
        plan = await versions.make_plan(loader, minecraft)
    except versions.VersionError as exc:
        raise ModpackError(str(exc)) from exc
    key = {"fabric": "fabric-loader", "quilt": "quilt-loader"}[loader]
    return {key: str(plan.loader)}


async def _dependencies(
    ctx: ServerContext, items: list[Item], players_loader: str
) -> dict[str, str]:
    """Minecraft and the loader, as the console reported them (never guessed)."""
    minecraft = ctx.server.mc_version
    if not minecraft:
        raise ModpackError(f"Start {ctx.name} once so the app knows its Minecraft version.")
    found = {"minecraft": minecraft}
    server_type = ctx.mods.server_type
    if server_type.content == "mods" and server_type.mrpack_dependency:
        row = ctx.core.db.server_row(ctx.server_id) or {}
        loader = ctx.server.loader_version or row.get("loader")
        if not loader:
            raise ModpackError(
                f"Start {ctx.name} once so the app knows its {server_type.name} version."
            )
        found[server_type.mrpack_dependency] = str(loader).removeprefix(f"{minecraft}-")
    elif any(i.path.startswith("mods/") for i in items):
        found.update(await _players_loader(ctx, players_loader, minecraft))
    return found


# ---------------------------------------------------------------- making it
def _summary(own: bool, get_yourself: list[str], server: str) -> str:
    words = [f"Made with Command Block from {server}."]
    if own:
        words.append(TRUST_NOTE)
    if get_yourself:
        words.append("Not inside, get these yourself: " + ", ".join(get_yourself) + ".")
    return " ".join(words)[:1000]


def _write(target: Path, index: dict[str, Any], members: list[tuple[Path, str]], job) -> None:
    """The zip, streamed from disk. Jars and zips are already squeezed, so
    they are stored as they are (less work for a weak PC)."""
    temp = target.with_name(f"{target.name}.part")
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(INDEX, json.dumps(index, indent=1))
            for source, name in members:
                stored = Path(name).suffix.lower() in (".jar", ".zip")
                zf.write(source, name, zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
                if job:
                    job.advance(1)
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def save_title_background(ctx: ServerContext, data: bytes) -> dict[str, Any]:
    """The owner's title-screen picture, kept for the next modpacks."""
    titlescreen.save_background(_folder(ctx), data)
    return options(ctx)


def _folder(ctx: ServerContext) -> Path:
    folder = ctx.core.config.data_dir / FOLDER / ctx.server_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _file(ctx: ServerContext, token: str) -> Path:
    return ctx.core.config.data_dir / FOLDER / ctx.server_id / f"{token}.mrpack"


def file_path(ctx: ServerContext, token: str) -> Path:
    """The newest modpack's file, for saving it."""
    last = made(ctx)
    if not TOKEN_RE.match(token or "") or not last or last["token"] != token:
        raise ModpackError("That modpack isn't there any more. Make it again.")
    return _file(ctx, token)


async def make(
    ctx: ServerContext,
    name: str,
    version: str,
    leave_out_server_only: bool = True,
    configs: list[str] | None = None,
    players_loader: str = "fabric",
    user: str = "system",
    required: bool = False,
    chat_line: dict[str, Any] | None = None,
    title_screen: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make the modpack, as a job. On the server it only writes the
    Companion's settings (agent/companion.py)."""
    if ctx.mods.server_type.edition == "bedrock":
        raise ModpackError("Bedrock servers can't make a modpack. Modpacks are for Java.")
    if not NAME_RE.match(name or "") or name != name.strip():
        raise ModpackError(
            'Give the modpack a name of up to 64 letters, without \\ / : * ? " < > |.'
        )
    if not VERSION_RE.match(version or ""):
        raise ModpackError("A version is up to 32 letters, numbers, dots or dashes, like 1.2.")
    chat_line = chat_line or {"on": True, "text": companion.CHAT_LINE_DEFAULT}
    if not companion.chat_line_ok(chat_line.get("text") or ""):
        raise ModpackError(
            f"The chat line is one line of up to {companion.CHAT_LINE_MAX} characters. It can "
            "use {name}, {version}, {link} and {get_it}, and no other { }."
        )
    if required and not await asyncio.to_thread(companion.on_server, ctx.mods):
        raise ModpackError(
            "Required to join needs the Command Block Companion mod on this server. Add it "
            "on the Mods page, then make the modpack again."
        )
    title_screen = titlescreen.check(title_screen, _folder(ctx))
    last = ctx.db.get_setting(MADE)
    if titlescreen.address_moved(last if isinstance(last, dict) else {}, title_screen, version):
        raise ModpackError(
            "The join button's address changed: give the modpack a new version, so "
            "friends' games pick up the new address."
        )
    offered = {c["path"] for c in config_files(ctx)}
    picked = list(dict.fromkeys(configs or []))
    unknown = [c for c in picked if c not in offered]
    if unknown:
        raise ModpackError(f"{unknown[0]} isn't one of the config files that can go in.")
    base = _server_dir(ctx)
    title = f"Making a modpack from {ctx.name}"

    async def run(job: JobHandle) -> dict[str, Any]:
        job.step("Reading the server's files")
        items = _items(ctx)

        def hash_all() -> None:
            for item in items:
                _hash(item)

        await asyncio.to_thread(hash_all)
        job.step("Asking Modrinth and CurseForge which files they have")
        await _look_up(ctx, items)
        for_companion = companion.player_files(_server_dir(ctx), items)
        left_out = [i for i in items if leave_out_server_only and i.side == "server"]
        items = [i for i in items if i not in left_out]
        dependencies = await _dependencies(ctx, items, players_loader)

        files, members, own, get_yourself, plugins, own_jars = [], [], [], [], [], []
        for item in items:
            if item.plugin:
                plugins.append({"name": item.name, "site": "Modrinth" if item.url else item.site})
            if item.need:
                get_yourself.append(item.need)
            elif item.url:
                files.append(
                    {
                        "path": item.path,
                        "hashes": {"sha1": item.sha1, "sha512": item.sha512},
                        "env": ENV[item.side],
                        "downloads": [item.url],
                        "fileSize": item.size,
                    }
                )
            elif not item.plugin:
                own.append(f"{SIDE_FOLDER[item.side]}/{item.path}")
                members.append((item.source, own[-1]))
                if item.source.suffix.lower() == ".jar" and is_inside(base, item.source):
                    # Each can be put on Modrinth as its own mod (agent/modrinthshare.py).
                    own_jars.append(
                        {
                            "name": item.name,
                            "path": item.source.resolve().relative_to(base.resolve()).as_posix(),
                            "side": item.side,
                        }
                    )
        for relative in picked:
            source = base / relative
            if source.is_file() and not source.is_symlink() and is_inside(base, source):
                members.append((source, f"overrides/{relative}"))
        server_type = ctx.mods.server_type
        note = None
        if server_type.content == "plugins" and not leave_out_server_only:
            note = {"format": 1, "server_type": server_type.id, "plugins": plugins[:200]}
        size = sum(source.stat().st_size for source, _ in members)
        if size > MAX_PACK_BYTES:
            raise ModpackError("The files that would go inside add up to more than 1 GB.")

        need_names = [n["name"] for n in get_yourself]
        index = {
            "formatVersion": 1,
            "game": "minecraft",
            "versionId": version,
            "name": name,
            "summary": _summary(bool(own), need_names, ctx.name),
            "files": files,
            "dependencies": dependencies,
        }
        token = secrets.token_hex(16)
        target = _folder(ctx) / f"{token}.mrpack"
        job.step("Writing the modpack", total=len(members) or None, unit="files")
        await asyncio.to_thread(_write, target, index, members, job)
        extra: dict[str, str | bytes] = {
            f"client-overrides/{companion.PLAYERS_FILE}": companion.players_file(
                name,
                version,
                [f["path"] for f in for_companion],
                titlescreen.players_part(ctx.name, title_screen),
            ),
            **{
                f"client-overrides/{path}": data
                for path, data in titlescreen.files(base, title_screen, _folder(ctx)).items()
            },
        }
        if note:
            extra[f"server-overrides/{SERVER_NOTE}"] = json.dumps(note, indent=1)
        await asyncio.to_thread(_add_files, target, extra)
        link = _link_on(ctx)
        await asyncio.to_thread(
            companion.write,
            base,
            server_type.content,
            name,
            version,
            for_companion,
            link,
            required,
            chat_line,
        )
        return {
            "token": token,
            "files": len(files) + len(members),
            "own_files": own,
            "get_yourself": get_yourself,
            "plugins_to_get": [p["name"] for p in plugins if p["site"] != "Modrinth"],
            "left_out": [i.name for i in left_out],
            "size_bytes": target.stat().st_size,
            "own_jars": own_jars,
        }

    job, result = await ctx.core.jobs.run(
        "modpack_make", title, run, server_id=ctx.server_id, user=user
    )
    safe = re.sub(r"[^A-Za-z0-9 ._-]+", "", f"{name} {version}").strip() or "modpack"
    saved = {
        "token": result["token"],
        "filename": f"{safe}.mrpack",
        "name": name,
        "version": version,
        "size_bytes": result["size_bytes"],
        "made_at": time.time(),
        "trust_note": bool(result["own_files"]),
        "get_yourself": [n["name"] for n in result["get_yourself"]],
        "own_jars": result["own_jars"],
        "required": required,
        "chat_line": {"on": bool(chat_line["on"]), "text": chat_line["text"]},
        "title_screen": title_screen,
    }
    ctx.db.set_setting(MADE, saved)
    for old in _folder(ctx).glob("*.mrpack"):  # only the newest is kept
        if old.name != f"{result['token']}.mrpack":
            old.unlink(missing_ok=True)
    ctx.core.db.audit(
        "modpack_make",
        user=user,
        target=ctx.server_id,
        detail=f"{name} {version}",
        server_id=ctx.server_id,
    )
    return {
        **result,
        "job_id": job.id,
        "filename": saved["filename"],
        "trust_note": TRUST_NOTE if result["own_files"] else None,
        "discord": bool(_chat_channels(ctx))
        and (result["size_bytes"] <= DISCORD_LIMIT or bool(_link_on(ctx))),
    }


def _link_on(ctx: ServerContext) -> str | None:
    from .downloadlink import MODPACK_NAME, url

    return url(ctx, MODPACK_NAME)


def _add_files(target: Path, files: dict[str, str | bytes]) -> None:
    with zipfile.ZipFile(target, "a", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            zf.writestr(name, text)


# ---------------------------------------------------------------- the Discord bot
async def post_to_discord(ctx: ServerContext, token: str, user: str = "system") -> dict[str, Any]:
    """The bot posts the newest modpack in this server's chat channels."""
    from .downloadlink import MODPACK_NAME, url

    path = file_path(ctx, token)
    last = made(ctx) or {}
    link = None
    if path.stat().st_size > DISCORD_LIMIT:
        # Too big for Discord: the download link instead, when it's on.
        link = url(ctx, MODPACK_NAME)
        if not link:
            raise ModpackError(
                "The modpack is bigger than Discord's 10 MB limit, so the bot can't post it. "
                "Turn on the download link and the bot posts that instead, or share the file "
                "another way."
            )
    channels = _chat_channels(ctx)
    if not channels:
        raise ModpackError(
            "The Discord bot isn't on, or this server has no chat channel in Discord."
        )
    words = (
        f"**{last['name']} {last['version']}**, the modpack for {ctx.name}. Open it with the "
        "Modrinth App or Prism Launcher."
    )
    if last.get("trust_note"):
        words += f"\n{TRUST_NOTE}"
    if last.get("get_yourself"):
        words += "\nNot inside, get these yourself: " + ", ".join(last["get_yourself"])
    filename = safe_filename(last["filename"], {".mrpack"})
    files = None
    if link:
        words += f"\nDownload it (works for friends on Tailscale): <{link}>"
    else:
        data = await asyncio.to_thread(path.read_bytes)
        files = {"files[0]": (filename, data, "application/zip")}
    bot = ctx.core.discord
    for channel in channels:
        try:
            await bot.rest.request(
                "POST",
                f"/channels/{channel}/messages",
                {"content": words[:1900], "allowed_mentions": {"parse": []}},
                files=files,
            )
        except DiscordError as exc:
            raise ModpackError(f"Discord didn't take the modpack: {exc}") from exc
    ctx.core.db.audit(
        "modpack_discord",
        user=user,
        target=ctx.server_id,
        detail=filename,
        server_id=ctx.server_id,
    )
    return {"posted": len(channels), "as_link": bool(link)}
