"""The download link, and the server resource pack it can serve.

The download link lets friends fetch a server's newest made modpack
(agent/modpackmake.py) and its resource pack straight from this PC:

  * off by default, turned on per server. Each server gets a random
    128-bit token in the link; "New link" makes a new one, and turning the
    link off forgets it, so an old link stops working at once
  * a tiny file server of its own, not the agent's API. It understands one
    kind of request, ``GET /<token>/<file>``, and can only send the two
    files that token stands for. No pages, no folders, nothing else
  * it listens only on the Tailscale address, the way friends join today,
    and only after the agent moved onto Tailscale (agent/tailscale_watch.py),
    so no command runs to find the address. Opening it to the internet
    comes with "Let friends join from the internet" (UPnP), later
  * limits like Join protection's: 2 downloads at once per address (IPv6
    counted per /64), 30 asks a minute per address, 8 downloads at once in
    all, 5 seconds to send the request, and a speed cap per download so a
    big pack can't fill a weak PC's network

The server resource pack is Minecraft's own: the game downloads it when a
player joins. The owner picks a zip (the app works out its SHA-1 and serves
it through the download link) or pastes an outside link. The app writes
``resource-pack``, ``resource-pack-sha1`` and ``require-resource-pack`` in
server.properties through the safe game-settings writer (one backup, one
Undo), and rewrites them when the link changes.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
import shutil
import time
import zipfile
from collections import defaultdict, deque
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from . import companion, gamesettings, modpackmake
from .joinguard import address_key
from .minecraft import properties
from .modpack import ModpackError
from .ports import is_free

if TYPE_CHECKING:
    from .core import AgentCore, ServerContext

log = logging.getLogger("msc.downloadlink")

LINK = "download_link"  # per server: {"on": bool, "token": str}
PACK = "resource_pack"  # per server: source, filename, url, sha1, size_bytes, required
PORT = "download_link_port"  # agent-wide: the port, kept so links stay the same
FIRST_PORT = 25590
PORTS_TRIED = 100
PACK_FOLDER = "resource-packs"
PACK_FILE = "pack.zip"
MODPACK_NAME = "modpack.mrpack"
PACK_NAME = "resource-pack.zip"
# Minecraft downloads server resource packs up to 250 MiB (since 1.18).
MAX_PACK_BYTES = 250 * 1024 * 1024
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
# Not measured on the low-end mini PC yet: a guess on the safe side.
SPEED_CAP = 4 * 1024 * 1024  # bytes a second, per download
CHUNK = 64 * 1024
PER_ADDRESS = 2
IN_ALL = 8
ASKS_A_MINUTE = 30
REQUEST_SECONDS = 5.0
MAX_REQUEST = 8192
HEADERS = (
    "Content-Type: application/zip\r\n"
    "Content-Security-Policy: default-src 'none'\r\n"
    "X-Content-Type-Options: nosniff\r\n"
    "Cache-Control: no-store\r\n"
    "Connection: close\r\n"
)
NO_TAILSCALE = (
    "Friends reach the download link over Tailscale. Finish the Tailscale step in the "
    "desktop app first."
)


# ---------------------------------------------------------------- per server
def _link(ctx: ServerContext) -> dict[str, Any]:
    saved = ctx.db.get_setting(LINK)
    return saved if isinstance(saved, dict) else {}


def _pack(ctx: ServerContext) -> dict[str, Any]:
    saved = ctx.db.get_setting(PACK)
    return saved if isinstance(saved, dict) else {}


def pack_file(ctx: ServerContext) -> Path:
    return ctx.core.config.data_dir / PACK_FOLDER / ctx.server_id / PACK_FILE


def _java_only(ctx: ServerContext) -> None:
    if ctx.config.server_type.edition == "bedrock":
        raise ModpackError("Bedrock servers get packs as add-ons, not through the download link.")


def _need_properties(ctx: ServerContext) -> None:
    _java_only(ctx)
    if not (ctx.config.server_dir / properties.FILENAME).is_file():
        raise ModpackError("server.properties isn't there yet. Start the server once.")


def url(ctx: ServerContext, name: str) -> str | None:
    """A file's address in the link, while the link is on and listening."""
    token = _link(ctx).get("token")
    hub: LinkServer = ctx.core.download_link
    if not _link(ctx).get("on") or not token or not hub.listening:
        return None
    host = hub.host or ""
    host = f"[{host}]" if ":" in host else host
    return f"http://{host}:{hub.port}/{token}/{name}"


def state(ctx: ServerContext) -> dict[str, Any]:
    """What the Download link card shows."""
    _java_only(ctx)
    hub: LinkServer = ctx.core.download_link
    link = _link(ctx)
    pack = _pack(ctx)
    return {
        "on": bool(link.get("on")),
        "why_not": None if hub.address() else NO_TAILSCALE,
        "listening": hub.listening,
        "modpack_url": url(ctx, MODPACK_NAME) if modpackmake.made(ctx) else None,
        "speed_cap_bytes": SPEED_CAP,
        "resource_pack": {
            "source": pack.get("source"),
            "filename": pack.get("filename"),
            "url": pack_url(ctx),
            "sha1": pack.get("sha1") or None,
            "size_bytes": pack.get("size_bytes"),
            "required": bool(pack.get("required")),
            "served": pack.get("source") == "file",
        }
        if pack.get("source")
        else None,
        "max_pack_bytes": MAX_PACK_BYTES,
    }


def pack_url(ctx: ServerContext) -> str | None:
    pack = _pack(ctx)
    if pack.get("source") == "link":
        return pack.get("url")
    if pack.get("source") == "file" and pack_file(ctx).is_file():
        return url(ctx, PACK_NAME)
    return None


def _relink(ctx: ServerContext) -> None:
    """The Companion hands players the link to update from: keep it current."""
    last = modpackmake.made(ctx)
    if last and ctx.config.server_dir_configured:
        link = url(ctx, MODPACK_NAME)
        companion.relink(ctx.config.server_dir, ctx.mods.server_type.content, last, link)


def _audit(ctx: ServerContext, action: str, user: str, detail: str = "") -> None:
    ctx.core.db.audit(
        action, user=user, target=ctx.server_id, detail=detail, server_id=ctx.server_id
    )


async def set_on(ctx: ServerContext, on: bool, user: str = "system") -> dict[str, Any]:
    """Turn the link on (a new token) or off (the token is forgotten)."""
    _java_only(ctx)
    hub: LinkServer = ctx.core.download_link
    if on and not hub.address():
        raise ModpackError(NO_TAILSCALE)
    was = _link(ctx)
    if bool(was.get("on")) == on:
        await hub.ensure()  # e.g. on, but Tailscale wasn't there when the agent started
        return state(ctx)
    ctx.db.set_setting(LINK, {"on": True, "token": secrets.token_hex(16)} if on else {})
    await hub.ensure()
    if on and not hub.listening:
        ctx.db.set_setting(LINK, {})
        raise ModpackError(
            "The download link couldn't start: no free port between "
            f"{FIRST_PORT} and {FIRST_PORT + PORTS_TRIED - 1} on the Tailscale address."
        )
    _audit(ctx, "download_link_on" if on else "download_link_off", user)
    await _write_pack(ctx, user)
    _relink(ctx)
    return state(ctx)


async def new_link(ctx: ServerContext, user: str = "system") -> dict[str, Any]:
    """A new token: the old link stops working at once."""
    _java_only(ctx)
    if not _link(ctx).get("on"):
        raise ModpackError("Turn the download link on first.")
    ctx.db.set_setting(LINK, {"on": True, "token": secrets.token_hex(16)})
    _audit(ctx, "download_link_new", user)
    await _write_pack(ctx, user)
    _relink(ctx)
    return state(ctx)


# ---------------------------------------------------------------- the resource pack
def check_pack(path: Path) -> str:
    """A resource pack is a zip with pack.mcmeta at the top. Its SHA-1."""
    if path.stat().st_size > MAX_PACK_BYTES:
        raise ModpackError("That resource pack is bigger than 250 MB, the most Minecraft takes.")
    try:
        with zipfile.ZipFile(path) as zf:
            if "pack.mcmeta" not in zf.namelist():
                raise ModpackError(
                    "That zip isn't a resource pack: it has no pack.mcmeta at the top."
                )
    except zipfile.BadZipFile:
        raise ModpackError("That file isn't a zip, so it can't be a resource pack.") from None
    sha1 = hashlib.sha1()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            sha1.update(chunk)
    return sha1.hexdigest()


async def set_pack_file(
    ctx: ServerContext, upload: Path, filename: str, required: bool, user: str = "system"
) -> dict[str, Any]:
    """Keep an uploaded zip as this server's resource pack, served by the link."""
    _need_properties(ctx)
    sha1 = await asyncio.to_thread(check_pack, upload)
    target = pack_file(ctx)
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.move, upload, target)
    ctx.db.set_setting(
        PACK,
        {
            "source": "file",
            "filename": Path(filename).name[:100] or PACK_NAME,
            "sha1": sha1,
            "size_bytes": target.stat().st_size,
            "required": required,
        },
    )
    _audit(ctx, "resource_pack_set", user, Path(filename).name[:100])
    return {**state(ctx), **await _write_pack(ctx, user)}


async def set_pack_link(
    ctx: ServerContext, link: str, sha1: str, required: bool, user: str = "system"
) -> dict[str, Any]:
    """Use a resource pack someone else hosts. The SHA-1 is the owner's to
    give (from the site); the app never makes one up."""
    _need_properties(ctx)
    link, sha1 = (link or "").strip(), (sha1 or "").strip().lower()
    parsed = urlparse(link)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or len(link) > 1000
        or any(c.isspace() or ord(c) < 32 for c in link)
    ):
        raise ModpackError("Paste the pack's link, starting with https://.")
    if sha1 and not SHA1_RE.match(sha1):
        raise ModpackError(
            "A SHA-1 is 40 letters and numbers (0-9, a-f). Leave it empty if the site doesn't give one."
        )
    pack_file(ctx).unlink(missing_ok=True)
    ctx.db.set_setting(PACK, {"source": "link", "url": link, "sha1": sha1, "required": required})
    _audit(ctx, "resource_pack_set", user, parsed.hostname)
    return {**state(ctx), **await _write_pack(ctx, user)}


async def set_required(ctx: ServerContext, required: bool, user: str = "system") -> dict[str, Any]:
    """ "Players must accept the resource pack" (Minecraft's require-resource-pack)."""
    _java_only(ctx)
    pack = _pack(ctx)
    if not pack.get("source"):
        raise ModpackError("Add a resource pack first.")
    ctx.db.set_setting(PACK, {**pack, "required": required})
    _audit(ctx, "resource_pack_required" if required else "resource_pack_optional", user)
    return {**state(ctx), **await _write_pack(ctx, user)}


async def remove_pack(ctx: ServerContext, user: str = "system") -> dict[str, Any]:
    _java_only(ctx)
    if not _pack(ctx).get("source"):
        return state(ctx)
    result = await _write_pack(ctx, user, values=_values(None, None, False))
    ctx.db.set_setting(PACK, {})
    pack_file(ctx).unlink(missing_ok=True)
    _audit(ctx, "resource_pack_remove", user)
    return {**state(ctx), **result}


def _values(link: str | None, sha1: str | None, required: bool) -> dict[str, str]:
    return {
        "resource-pack": link or "",
        "resource-pack-sha1": (sha1 or "") if link else "",
        "require-resource-pack": "true" if link and required else "false",
    }


async def _write_pack(
    ctx: ServerContext, user: str, values: dict[str, str] | None = None
) -> dict[str, Any]:
    """Make server.properties say what the pack is now. A pack served by
    the link while the link is off is taken out (kept, and put back when the
    link is on again). Nothing to do when no pack was ever set."""
    pack = _pack(ctx)
    if values is None:
        if not pack.get("source"):
            return {}
        values = _values(pack_url(ctx), pack.get("sha1"), bool(pack.get("required")))
    written = await gamesettings.set_values(
        ctx, values, "Setting the server resource pack", user=user
    )
    return {
        "undo": written.get("undo"),
        "restart_needed": bool(written.get("changed")) and ctx.server.running,
    }


# ---------------------------------------------------------------- the file server
class LinkServer:
    """The one listener for every server's link. Runs on the agent's loop;
    files are read off it, a chunk at a time."""

    def __init__(self, core: AgentCore):
        self.core = core
        self.server: asyncio.base_events.Server | None = None
        self.host: str | None = None
        self.port: int | None = None
        self.active: dict[str, int] = defaultdict(int)
        self.asks: dict[str, deque[float]] = defaultdict(deque)

    @property
    def listening(self) -> bool:
        return self.server is not None

    def address(self) -> str | None:
        """The Tailscale address the agent moved onto, if it did."""
        watch = getattr(self.core, "tailscale", None)
        return self.core.config.network.host if watch is not None and watch.moved else None

    def _wanted(self) -> bool:
        return any(_link(ctx).get("on") for ctx in self.core.servers.values())

    def _port(self, host: str) -> int | None:
        saved = self.core.db.get_setting(PORT)
        taken = {port for (_kind, port) in self.core.ports.used()}
        tries = ([saved] if isinstance(saved, int) else []) + list(
            range(FIRST_PORT, FIRST_PORT + PORTS_TRIED)
        )
        for port in tries:
            if port not in taken and is_free(port, "tcp", host):
                self.core.db.set_setting(PORT, port)
                return port
        return None

    async def ensure(self) -> None:
        """Listen while any server's link is on, else close."""
        self.host = self.address()
        if not (self.host and self._wanted()):
            await self.stop()
            return
        if self.server is not None:
            return
        port = await asyncio.to_thread(self._port, self.host)
        if port is None:
            log.warning("no free port for the download link on %s", self.host)
            return
        try:
            self.server = await asyncio.start_server(self._serve, self.host, port)
        except OSError:
            log.exception("the download link couldn't listen on %s:%s", self.host, port)
            return
        self.port = port
        log.info("download link listening on %s:%s", self.host, port)

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            self.server = None
            self.port = None

    # -- one request -----------------------------------------------------------
    def _find(self, token: str, name: str) -> tuple[Path, str] | None:
        """The file a token and name stand for, or None."""
        if not modpackmake.TOKEN_RE.match(token):
            return None
        for ctx in self.core.servers.values():
            link = _link(ctx)
            if not link.get("on") or not secrets.compare_digest(str(link.get("token")), token):
                continue
            if name == MODPACK_NAME:
                made = modpackmake.made(ctx)
                if made:
                    return modpackmake.file_path(ctx, made["token"]), made["filename"]
            elif name == PACK_NAME and _pack(ctx).get("source") == "file":
                path = pack_file(ctx)
                if path.is_file():
                    return path, _pack(ctx).get("filename") or PACK_NAME
            return None
        return None

    def _allowed(self, key: str) -> bool:
        now = time.monotonic()
        asks = self.asks[key]
        while asks and now - asks[0] > 60:
            asks.popleft()
        asks.append(now)
        return (
            len(asks) <= ASKS_A_MINUTE
            and self.active[key] < PER_ADDRESS
            and sum(self.active.values()) < IN_ALL
        )

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        key = address_key(str(peer[0])) if peer[0] != "?" else "?"
        counted = False
        try:
            if not self._allowed(key):
                await _answer(writer, "429 Too Many Requests")
                return
            self.active[key] += 1
            counted = True
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), REQUEST_SECONDS)
            except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                await _answer(writer, "400 Bad Request")
                return
            if len(head) > MAX_REQUEST:
                await _answer(writer, "400 Bad Request")
                return
            parts = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ")
            if len(parts) != 3 or parts[0] != "GET" or not parts[2].startswith("HTTP/1."):
                await _answer(writer, "405 Method Not Allowed")
                return
            pieces = parts[1].split("/")
            found = (
                self._find(pieces[1], pieces[2]) if len(pieces) == 3 and pieces[0] == "" else None
            )
            if found is None:
                await _answer(writer, "404 Not Found")
                return
            await _send(writer, *found)
        except (ConnectionError, OSError):
            pass
        except Exception:  # one bad download must never stop the listener
            log.exception("download link request failed")
        finally:
            if counted:
                self.active[key] -= 1
                if not self.active[key]:
                    del self.active[key]
            writer.close()


async def _answer(writer: asyncio.StreamWriter, status: str) -> None:
    writer.write(
        f"HTTP/1.1 {status}\r\nContent-Length: 0\r\n"
        "Content-Security-Policy: default-src 'none'\r\nConnection: close\r\n\r\n".encode()
    )
    await writer.drain()


async def _send(writer: asyncio.StreamWriter, path: Path, filename: str) -> None:
    """The file, no faster than SPEED_CAP."""
    safe = re.sub(r"[^\w .+-]", "_", filename)[:100] or "download.zip"
    with path.open("rb") as f:
        size = f.seek(0, 2)
        f.seek(0)
        writer.write(
            (
                f"HTTP/1.1 200 OK\r\n{HEADERS}Content-Length: {size}\r\n"
                f'Content-Disposition: attachment; filename="{safe}"\r\n\r\n'
            ).encode()
        )
        started, sent = time.monotonic(), 0
        while chunk := await asyncio.to_thread(f.read, CHUNK):
            writer.write(chunk)
            await writer.drain()
            sent += len(chunk)
            ahead = sent / SPEED_CAP - (time.monotonic() - started)
            if ahead > 0:
                await asyncio.sleep(ahead)
