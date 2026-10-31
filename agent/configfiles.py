"""Edit settings: the text config files in a server's folder, edited safely.

Lists the add-ons' config files (``plugins/<name>/`` on plugin servers,
``config/<mod id>...`` on mod servers) and every other text config file in
the server folder (.json, .toml, .yml, .yaml, .properties, .txt, .conf). Only files
inside the server folder, never through ``..`` or a link, never bigger than
MAX_BYTES and never a file that isn't UTF-8 text. ``server.properties`` and
the player lists stay on their own pages.

Saving:
  1. the file must still be the one that was opened (its sha256)
  2. hidden values the app put back are the file's own again
  3. the text must still read as its format (JSON, TOML, YAML, properties);
     if not, the error says the line and nothing is written
  4. a backup of that one file is taken (the change's Undo), the file is
     written and read back. Running servers read it on their next start.

Values whose key looks like a token, password, secret or key are shown as
HIDDEN until the owner asks to see them, and each look is in the audit log.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .events import Event
from .safechange import SafeChange, run_safe_change
from .security.paths import PathSafetyError, assert_not_symlink, safe_filename, safe_join

if TYPE_CHECKING:
    from .core import ServerContext
    from .jobs import JobHandle

FORMATS = {
    ".json": "json",
    ".toml": "toml",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".properties": "properties",
    ".txt": "text",
    ".conf": "text",  # HOCON (BlueMap, MiniMOTD): no checker installed, saved as typed
}
MAX_BYTES = 512 * 1024
MAX_FILES = 500
MAX_DEPTH = 5
# On their own pages: Game settings and Players (and the first run's EULA).
OWN_PAGE = {
    "server.properties",
    "whitelist.json",
    "allowlist.json",
    "ops.json",
    "permissions.json",
    "banned-players.json",
    "banned-ips.json",
    "usercache.json",
    "eula.txt",
}
# Folders that hold no settings: logs, downloads, the game's own caches.
SKIP_FOLDERS = {
    "logs",
    "crash-reports",
    "libraries",
    "versions",
    "cache",
    "debug",
    "bundler",
    "worlds",
    "behavior_packs",
    "resource_packs",
}
HIDDEN = "••••••"
SECRET_WORDS = {"token", "password", "passwd", "secret", "key", "apikey"}
# key = value, key: value or "key": value, with what is around the value kept.
LINE_RE = re.compile(
    r"""^(?P<head>\s*(?:-\s+)?(?P<q>["']?)(?P<key>[A-Za-z0-9_.\-]+)(?P=q)\s*[:=]\s*)"""
    r"""(?P<value>.*?)(?P<tail>\s*,?\s*)$"""
)


class ConfigFileError(ValueError):
    """Refused, with the line it is about when there is one."""

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.line = line


class FileChanged(ConfigFileError):
    pass


# ------------------------------------------------------------ which files
def _plain(name: str) -> bool:
    """A name the path rules accept, so what is listed also opens."""
    try:
        safe_filename(name)
    except PathSafetyError:
        return False
    return True


def _usable(name: str) -> bool:
    return ".replaced-" not in name and Path(name).suffix.lower() in FORMATS and _plain(name)


def _walk(base: Path, start: Path, skip: set[Path], found: list[str]) -> bool:
    """Add the usable files under ``start`` (relative to ``base``) to
    ``found``. False once MAX_FILES is reached."""
    for root, dirs, names in os.walk(start, followlinks=False):
        here = Path(root)
        depth = len(here.relative_to(base).parts)
        dirs[:] = sorted(
            d
            for d in dirs
            if depth < MAX_DEPTH
            and _plain(d)
            and not (depth == 0 and d.lower() in SKIP_FOLDERS)
            and not os.path.islink(here / d)
            and (here / d).resolve() not in skip
            and not (here / d / "level.dat").exists()
        )
        for name in sorted(names):
            full = here / name
            if not _usable(name) or full.is_symlink():
                continue
            rel = full.relative_to(base).as_posix()
            if depth == 0 and name.lower() in OWN_PAGE:
                continue
            if len(found) >= MAX_FILES:
                return False
            found.append(rel)
    return True


def _describe(base: Path, rel: str) -> dict[str, Any]:
    stat = (base / rel).stat()
    return {
        "path": rel,
        "format": FORMATS[Path(rel).suffix.lower()],
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "too_big": stat.st_size > MAX_BYTES,
    }


def server_files(ctx: ServerContext) -> dict[str, Any]:
    """Every usable config file in the server folder, except the add-ons'
    data folders' jars and the app's own folders kept inside it."""
    base = ctx.config.server_dir
    inside = {
        Path(p).resolve()
        for p in (ctx.config.data_dir, ctx.config.backup_dir, ctx.config.log_dir)
        if p
    }
    found: list[str] = []
    complete = _walk(base, base, inside, found) if base.is_dir() else True
    return {"files": [_describe(base, rel) for rel in found], "truncated": not complete}


def addon_files(ctx: ServerContext, filename: str) -> dict[str, Any]:
    """One add-on's config files: its plugin folder, or its files and
    folder in config/."""
    mod = ctx.mods.find(filename)
    base = ctx.config.server_dir
    found: list[str] = []
    name = mod.mod_id or ""
    if name and ctx.config.server_type.content == "plugins":
        try:
            folder = safe_join(ctx.config.mods_dir, name)
        except PathSafetyError:
            folder = None
        if folder and folder.is_dir() and not folder.is_symlink():
            _walk(base, folder, set(), found)
    elif name:
        config = base / "config"
        if config.is_dir() and not config.is_symlink():
            for entry in sorted(config.iterdir(), key=lambda e: e.name.lower()):
                if not entry.name.lower().startswith(name.lower()) or entry.is_symlink():
                    continue
                if entry.is_dir():
                    _walk(base, entry, set(), found)
                elif _usable(entry.name):
                    found.append(entry.relative_to(base).as_posix())
    return {
        "name": mod.name or mod.filename,
        "files": [_describe(base, rel) for rel in found],
    }


def resolve(ctx: ServerContext, rel: str) -> Path:
    """The file ``rel`` names in the server folder, or PathSafetyError: every
    part a plain name (no "..", no drive), no link anywhere on the way, an
    editable kind of file, not one with its own page."""
    raw = str(rel or "").replace("\\", "/")
    parts = [p for p in raw.split("/") if p]
    if not parts or raw.startswith("/") or any(p in (".", "..") for p in parts):
        raise PathSafetyError("That isn't a file in this server's folder.")
    if len(parts) == 1 and parts[0].lower() in OWN_PAGE:
        raise PathSafetyError(f"{parts[0]} is changed on its own page.")
    if not _usable(parts[-1]):
        raise PathSafetyError(
            "Only .json, .toml, .yml, .properties, .conf and .txt files open here."
        )
    base = ctx.config.server_dir
    path = safe_join(base, *parts)
    node = base
    for part in parts:
        node = node / part
        assert_not_symlink(node)
    if not path.is_file():
        raise PathSafetyError(f"{rel} isn't there.")
    return path


def _read(path: Path) -> tuple[bytes, str]:
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise ConfigFileError(
            f"{path.name} is bigger than {MAX_BYTES // 1024} KB, so it doesn't open here."
        )
    if b"\0" in data:
        raise ConfigFileError(f"{path.name} isn't a text file, so it doesn't open here.")
    try:
        return data, data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ConfigFileError(
            f"{path.name} isn't saved as UTF-8 text, so it doesn't open here."
        ) from None


def version_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------ hidden values
def is_secret(key: str) -> bool:
    words = re.split(r"[-_.\s]+|(?<=[a-z])(?=[A-Z])", key)
    return any(w.lower() in SECRET_WORDS for w in words if w)


def _secret_lines(text: str):
    """(line index, match) for each line holding a hidden value."""
    for index, line in enumerate(text.split("\n")):
        if line.lstrip().startswith(("#", "//", ";", "!")):
            continue
        found = LINE_RE.match(line.rstrip("\r"))
        if not found or not is_secret(found["key"]):
            continue
        value = found["value"]
        inner = value[1:-1] if len(value) >= 2 and value[0] == value[-1] in "\"'" else value
        if inner.strip() and value not in ("{", "["):
            yield index, found


def _swap(value: str, inner: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] in "\"'":
        return value[0] + inner + value[-1]
    return inner


def _inner(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] in "\"'" else value


def hide(text: str) -> tuple[str, list[dict[str, Any]]]:
    """The text with each hidden value as HIDDEN, and where they are."""
    lines = text.split("\n")
    hidden = []
    for index, found in _secret_lines(text):
        cr = "\r" if lines[index].endswith("\r") else ""
        lines[index] = found["head"] + _swap(found["value"], HIDDEN) + found["tail"] + cr
        hidden.append({"line": index + 1, "key": found["key"]})
    return "\n".join(lines), hidden


def put_back(edited: str, original: str) -> str:
    """The edited text with each value still HIDDEN set back to the file's
    own value for that key (the first hidden "password" gets the file's
    first "password", and so on). A HIDDEN that can't be matched is
    refused, so the mask is never written into the file."""
    own: dict[str, list[str]] = {}
    for _, found in _secret_lines(original):
        own.setdefault(found["key"], []).append(_inner(found["value"]))
    used: dict[str, int] = {}
    lines = edited.split("\n")
    for index, line in enumerate(lines):
        if HIDDEN not in line:
            continue
        found = LINE_RE.match(line.rstrip("\r"))
        if not found or _inner(found["value"]) != HIDDEN:
            raise ConfigFileError(
                "A hidden value was changed in a way the app can't put back. Show the "
                "hidden values first, then make the change.",
                index + 1,
            )
        key = found["key"]
        number = used.get(key, 0)
        values = own.get(key) or []
        if number >= len(values):
            raise ConfigFileError(
                f"The hidden value of {key} can't be matched to the file. Show the hidden "
                "values first, then make the change.",
                index + 1,
            )
        used[key] = number + 1
        cr = "\r" if line.endswith("\r") else ""
        lines[index] = found["head"] + _swap(found["value"], values[number]) + found["tail"] + cr
    return "\n".join(lines)


# ------------------------------------------------------------ the check
def check(text: str, kind: str) -> None:
    """ConfigFileError with the line when ``text`` doesn't read as ``kind``."""
    if "\0" in text:
        raise ConfigFileError("The text has a character in it a settings file can't hold.")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ConfigFileError(f"That is more than {MAX_BYTES // 1024} KB of text.")
    try:
        if kind == "json":
            json.loads(text)
        elif kind == "toml":
            tomllib.loads(text)
        elif kind == "yaml":
            yaml.safe_load(text)
    except json.JSONDecodeError as exc:
        raise ConfigFileError(f"Line {exc.lineno}: {exc.msg}.", exc.lineno) from None
    except tomllib.TOMLDecodeError as exc:
        line = getattr(exc, "lineno", None) or _line_in(str(exc))
        raise ConfigFileError(f"This isn't valid TOML: {exc}.", line) from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        problem = getattr(exc, "problem", None) or str(exc)
        where = f"Line {line}: " if line else ""
        raise ConfigFileError(f"{where}{problem}.", line) from None


def _line_in(message: str) -> int | None:
    found = re.search(r"line (\d+)", message)
    return int(found.group(1)) if found else None


# ------------------------------------------------------------ read, show, save
def read(ctx: ServerContext, rel: str, show: bool = False) -> dict[str, Any]:
    path = resolve(ctx, rel)
    data, text = _read(path)
    shown, hidden = (text, []) if show else hide(text)
    return {
        "path": rel,
        "format": FORMATS[path.suffix.lower()],
        "text": shown,
        "hidden": hidden,
        "version": version_of(data),
        "size": len(data),
        "running": ctx.server.running,
    }


async def save(
    ctx: ServerContext, rel: str, text: str, version: str, user: str = "system"
) -> dict[str, Any]:
    path = resolve(ctx, rel)
    data, current = _read(path)
    if version_of(data) != version:
        raise FileChanged(
            f"{path.name} changed on the PC since you opened it. Open it again to see the "
            "new version, then make your change."
        )
    edited = put_back(text.replace("\r\n", "\n"), current.replace("\r\n", "\n"))
    kind = FORMATS[path.suffix.lower()]
    check(edited, kind)
    if "\r\n" in current:
        edited = edited.replace("\n", "\r\n")
    raw = (b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b"") + edited.encode("utf-8")
    if raw == data:
        return {"nothing_changed": True, "path": rel, "running": ctx.server.running}

    async def change(job: JobHandle | None) -> dict[str, Any]:
        def write() -> None:
            temp = path.with_name(f".{path.name}.part")
            temp.write_bytes(raw)
            os.replace(temp, path)

        await asyncio.to_thread(write)
        return {"path": rel}

    async def check_written(result: dict[str, Any]) -> tuple[bool, str]:
        same = await asyncio.to_thread(path.read_bytes) == raw
        return same, f"{rel} reads back as saved" if same else f"{rel} didn't read back"

    plan = SafeChange(
        title=f"Changing {rel}",
        change=change,
        check=check_written,
        stop_server=False,
        backup_name="pre-settings-file",
        backup_includes=[rel],
    )

    async def run(job: JobHandle | None) -> dict[str, Any]:
        return await run_safe_change(ctx.server, ctx.backups, plan, job=job, user=user)

    _, outcome = await ctx.core.jobs.run(
        "config_file",
        f"Changing {rel} on {ctx.name}",
        run,
        server_id=ctx.server_id,
        risky=True,
        user=user,
    )
    await ctx.bus.publish(
        Event(
            type="config_file_changed",
            level="info",
            message=f"{rel} changed on {ctx.name}"
            + (". It takes effect after a restart." if ctx.server.running else "."),
            data={"path": rel, "running": ctx.server.running},
        )
    )
    return {
        "path": rel,
        "version": version_of(raw),
        "undo": outcome.get("undo"),
        "safety_backup": (outcome.get("safety_backup") or {}).get("name"),
        "running": ctx.server.running,
        "restart_needed": ctx.server.running,
    }
