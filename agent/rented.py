"""Rented servers: Minecraft servers on a host's Pterodactyl panel.

Most hosts (Sparked Host and many others) run Pterodactyl, which has one
"client API" for all of them. The owner connects a panel with its address
and a client API key (never the panel password, never the admin API) and
picks which of its servers to add. From then on this agent talks to the
panel, so moderators, alerts, Discord and the apps use a rented server
without ever seeing the key.

What a rented server can do here: status (every 20 s, slower after a
failure), start, stop, restart, Kill, the live console, and the host's
backups. Everything that needs the server's files on this PC (backups
here, Wake, Join protection, Change server type, add-ons) is greyed out
with the reason, and files, mods and safe changes come later.

Saved in the data folder: ``rented-servers.json`` (panels and servers,
nothing secret) and ``rented-keys.bin`` (the keys, encrypted for the
agent's Windows account with DPAPI, like the separate Windows user's
password). A key is never logged and never in an API answer.

Like a Java class with one HTTP client per panel (Panel) and one list
object (RentedServers) that the routes, the Discord bot and the poll loop
share.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import downloads
from .events import Event

log = logging.getLogger("msc.rented")

LIST_FILE = "rented-servers.json"
KEY_FILE = "rented-keys.bin"
POLL_SECONDS = 20  # Pterodactyl allows 240 calls a minute; this is far under
MAX_WAIT = 300  # after failures the wait doubles up to this
SLOW_DOWN_WAIT = 60  # after the panel answered "too many requests"
CONSOLE_IDLE = 60  # the console connection closes when nobody read it this long
CONSOLE_LINES = 500
MAX_ANSWER = 2 * 1024 * 1024
SIGNALS = ("start", "stop", "restart", "kill")
STATES = {
    "running": "ONLINE",
    "starting": "STARTING",
    "stopping": "STOPPING",
    "offline": "OFFLINE",
}
# Features that need the server's files on this PC (the apps grey them out
# and say why), and the ones that come later on a rented server.
NEEDS_THIS_PC = ("backups_here", "wake", "join_protection", "change_type", "addons")
LATER = ("files", "mods", "safe_changes")
DISCORD_CHOICES = ("off", "status", "control")
KILL_WARNING = (
    "Kill ends the server at once, without saving. The world can be damaged. "
    "Use Stop unless the server is stuck."
)


class PanelError(RuntimeError):
    """A panel call failed; the message is written for the owner."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def plain_words(status: int | None, fallback: str) -> str:
    if status == 401:
        return (
            "The panel refused the key. It may have been deleted: make a new client "
            "API key and connect the panel again."
        )
    if status == 403:
        return (
            "The key is missing a permission for that. On a sub-user, tick it in "
            "the panel's Users page."
        )
    if status == 404:
        return "The panel doesn't have that server any more."
    if status == 409:
        return "The panel says the server is busy (installing, moving or restoring)."
    if status == 429:
        return "The panel asked us to slow down. Command Block tries again in a minute."
    if status == 502:
        return "The server isn't running there, so the panel couldn't pass that on."
    if status is not None and status >= 500:
        return "The panel has a problem right now. Try again later."
    return fallback


def panel_address(text: str) -> str:
    """``https://host`` from what the owner pasted, or ValueError."""
    raw = str(text or "").strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        raise ValueError("The panel address has to start with https://.")
    host = (parsed.hostname or "").lower()
    if not host or "." not in host or parsed.username or parsed.password:
        raise ValueError("That isn't a panel address. Paste the address you open the panel at.")
    if parsed.port not in (None, 443):
        raise ValueError("Only panels on the normal https port are supported.")
    if parsed.path.strip("/") or parsed.query:
        raise ValueError("Paste only the panel's address, like https://panel.example.com.")
    return f"https://{host}"


class Panel:
    """One panel's client API, with one key."""

    def __init__(self, address: str, key: str) -> None:
        self.address = address
        self.host = urlparse(address).hostname or ""
        self.key = key

    async def call(self, method: str, path: str, body: dict | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json"}
        content = None
        if body is not None:
            content = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        url = f"{self.address}{downloads.PANEL_PATH}{path}"
        with downloads.allow_panel(self.host):
            try:
                async with downloads._client(timeout=15.0) as client:
                    response, final = await downloads._send(
                        client, url, stream=True, headers=headers, method=method, content=content
                    )
                    raw = await downloads._read(response, final, MAX_ANSWER)
            except downloads.DownloadError as exc:
                raise PanelError(plain_words(exc.status, str(exc)), exc.status) from None
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise PanelError("That address didn't answer like a Pterodactyl panel.") from None

    async def servers(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for page in range(1, 11):  # 50 a page; more than 500 servers isn't a home owner
            answer = await self.call("GET", f"?page={page}")
            if not isinstance(answer, dict) or not isinstance(answer.get("data"), list):
                raise PanelError("That address didn't answer like a Pterodactyl panel.")
            for item in answer["data"]:
                a = item.get("attributes") or {}
                if a.get("identifier"):
                    found.append(
                        {
                            "identifier": str(a["identifier"]),
                            "name": str(a.get("name") or a["identifier"])[:80],
                            "node": a.get("node"),
                            "server_owner": bool(a.get("server_owner")),
                        }
                    )
            pages = ((answer.get("meta") or {}).get("pagination") or {}).get("total_pages") or 1
            if page >= pages:
                break
        return found


@dataclass
class RentedServer:
    id: str
    panel_id: str
    identifier: str
    name: str
    discord: str = "off"
    # Measured, never saved: Unknown until the panel answered.
    state: str = "UNKNOWN"
    reason: str | None = "Not asked yet."
    resources: dict[str, Any] | None = None
    checked_at: float | None = None
    console: RentedConsole | None = field(default=None, repr=False)

    def saved(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "panel_id": self.panel_id,
            "identifier": self.identifier,
            "name": self.name,
            "discord": self.discord,
        }


class RentedServers:
    def __init__(self, core: Any) -> None:
        self.core = core
        folder = Path(core.config.data_dir)
        self.list_path = folder / LIST_FILE
        self.key_path = folder / KEY_FILE
        self.panels: dict[str, dict[str, str]] = {}
        self.servers: dict[str, RentedServer] = {}
        self.wait = POLL_SECONDS
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._load()

    # -------------------------------------------------------------- saving
    def _load(self) -> None:
        try:
            saved = json.loads(self.list_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            log.warning("rented-servers.json couldn't be read; no rented servers are shown")
            return
        self.panels = {str(k): dict(v) for k, v in (saved.get("panels") or {}).items()}
        for row in saved.get("servers") or []:
            if row.get("panel_id") in self.panels:
                server = RentedServer(
                    id=row["id"],
                    panel_id=row["panel_id"],
                    identifier=row["identifier"],
                    name=row.get("name") or row["identifier"],
                    discord=row.get("discord") if row.get("discord") in DISCORD_CHOICES else "off",
                )
                self.servers[server.id] = server

    def _save(self) -> None:
        body = {
            "panels": self.panels,
            "servers": [s.saved() for s in self.servers.values()],
        }
        tmp = self.list_path.with_suffix(".tmp")
        self.list_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(body, indent=2), encoding="utf-8")
        tmp.replace(self.list_path)

    def _keys(self) -> dict[str, str]:
        try:
            raw = self.key_path.read_bytes()
            if os.name == "nt":
                from .security.records import _dpapi

                raw = _dpapi(raw, protect=False)
            keys = json.loads(raw)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log.warning("rented-keys.bin couldn't be read")
            return {}
        return {str(k): str(v) for k, v in keys.items()}

    def _write_keys(self, keys: dict[str, str]) -> None:
        from .security.certs import _restrict_file

        raw = json.dumps(keys).encode()
        if os.name == "nt":
            from .security.records import _dpapi

            raw = _dpapi(raw, protect=True)
        tmp = self.key_path.with_suffix(".tmp")
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(raw)
        _restrict_file(tmp)
        tmp.replace(self.key_path)

    def panel_of(self, server: RentedServer) -> Panel:
        key = self._keys().get(server.panel_id)
        if not key:
            raise PanelError("The panel's key is missing. Connect the panel again.", 401)
        return Panel(self.panels[server.panel_id]["address"], key)

    # -------------------------------------------------------------- the list
    def get(self, server_id: str) -> RentedServer:
        found = self.servers.get(server_id)
        if found is None:
            raise KeyError(server_id)
        return found

    def names(self) -> dict[str, str]:
        return {s.id: s.name for s in self.servers.values()}

    def to_dict(self, server: RentedServer) -> dict[str, Any]:
        panel = self.panels[server.panel_id]
        return {
            "id": server.id,
            "kind": "pterodactyl",
            "name": server.name,
            "host": panel["host"],
            "panel_id": server.panel_id,
            "panel_address": panel["address"],
            "identifier": server.identifier,
            "state": server.state,
            "state_reason": server.reason,
            "resources": server.resources,
            "checked_at": server.checked_at,
            "discord": server.discord,
            "needs_this_pc": list(NEEDS_THIS_PC),
            "later": list(LATER),
        }

    async def check_panel(self, address: str, key: str) -> dict[str, Any]:
        """The panel's servers for this key, before anything is saved."""
        address = panel_address(address)
        key = str(key or "").strip()
        if not key or len(key) > 200 or any(c.isspace() for c in key):
            raise ValueError("Paste the client API key you made in the panel.")
        found = await Panel(address, key).servers()
        return {
            "address": address,
            "host": urlparse(address).hostname,
            "servers": found,
            # A key of the main account can do everything that account can;
            # a sub-user's key only what was ticked for it.
            "full_account": any(s["server_owner"] for s in found),
        }

    async def connect(
        self, address: str, key: str, identifiers: list[str], user: str
    ) -> list[dict[str, Any]]:
        checked = await self.check_panel(address, key)
        on_panel = {s["identifier"]: s for s in checked["servers"]}
        wanted = [i for i in dict.fromkeys(identifiers) if i in on_panel]
        if not wanted:
            raise ValueError("Pick at least one of the panel's servers.")
        async with self._lock:
            panel_id = next(
                (pid for pid, p in self.panels.items() if p["address"] == checked["address"]),
                None,
            ) or "panel-" + secrets.token_hex(4)
            self.panels[panel_id] = {"address": checked["address"], "host": checked["host"]}
            keys = self._keys()
            keys[panel_id] = key.strip()
            self._write_keys(keys)
            have = {(s.panel_id, s.identifier) for s in self.servers.values()}
            added = []
            for identifier in wanted:
                if (panel_id, identifier) in have:
                    continue
                server = RentedServer(
                    id="rented-" + secrets.token_hex(4),
                    panel_id=panel_id,
                    identifier=identifier,
                    name=on_panel[identifier]["name"],
                )
                self.servers[server.id] = server
                added.append(server)
            self._save()
        self.core.db.audit("rented_connect", user=user, target=checked["host"])
        for server in added:
            await self.poll_one(server)
        self.start()
        return [self.to_dict(s) for s in added]

    async def remove_key(self, panel_id: str, user: str) -> dict[str, Any]:
        """Delete the panel's key and take its servers off the list."""
        async with self._lock:
            panel = self.panels.pop(panel_id, None)
            if panel is None:
                raise KeyError(panel_id)
            gone = [s for s in self.servers.values() if s.panel_id == panel_id]
            for server in gone:
                if server.console:
                    server.console.close()
                del self.servers[server.id]
            keys = self._keys()
            keys.pop(panel_id, None)
            self._write_keys(keys)
            self._save()
        self.core.db.audit("rented_remove_key", user=user, target=panel["host"])
        return {"removed": [s.id for s in gone], "host": panel["host"]}

    def set_discord(self, server: RentedServer, choice: str) -> None:
        if choice not in DISCORD_CHOICES:
            raise ValueError("Pick off, status or control.")
        server.discord = choice
        self._save()

    # -------------------------------------------------------------- calls
    async def power(self, server: RentedServer, signal: str) -> None:
        if signal not in SIGNALS:
            raise ValueError("That isn't something a server can be told to do.")
        await self.panel_of(server).call(
            "POST", f"/servers/{server.identifier}/power", {"signal": signal}
        )

    async def command(self, server: RentedServer, command: str) -> None:
        await self.panel_of(server).call(
            "POST", f"/servers/{server.identifier}/command", {"command": command}
        )

    async def backups(self, server: RentedServer) -> dict[str, Any]:
        answer = await self.panel_of(server).call("GET", f"/servers/{server.identifier}/backups")
        rows = []
        for item in (answer or {}).get("data") or []:
            a = item.get("attributes") or {}
            rows.append(
                {
                    "id": a.get("uuid"),
                    "name": a.get("name"),
                    "bytes": a.get("bytes"),
                    "created_at": a.get("created_at"),
                    "completed_at": a.get("completed_at"),
                    # None while the host is still making it.
                    "succeeded": a.get("is_successful") if a.get("completed_at") else None,
                    "locked": bool(a.get("is_locked")),
                }
            )
        # How many backups the host's plan allows, when the panel says.
        limit = ((answer or {}).get("meta") or {}).get("backup_limit")
        return {"backups": rows, "limit": limit}

    async def make_backup(self, server: RentedServer) -> None:
        await self.panel_of(server).call("POST", f"/servers/{server.identifier}/backups", {})

    # -------------------------------------------------------------- status
    async def poll_one(self, server: RentedServer) -> PanelError | None:
        before = server.state
        try:
            answer = await self.panel_of(server).call(
                "GET", f"/servers/{server.identifier}/resources"
            )
            a = (answer or {}).get("attributes") or {}
            if a.get("is_suspended"):
                raise PanelError("The host has suspended this server.")
            state = STATES.get(str(a.get("current_state")))
            if state is None:
                raise PanelError("The panel didn't say whether the server is running.")
        except PanelError as exc:
            if server.id not in self.servers:  # its key was removed meanwhile
                return None
            server.state, server.reason, server.resources = "UNKNOWN", str(exc), None
            server.checked_at = time.time()
            if before != "UNKNOWN":
                await self._tell(server, "rented_unreachable", "warn", str(exc))
            return exc
        if server.id not in self.servers:
            return None
        r = a.get("resources") or {}
        server.state, server.reason = state, None
        server.checked_at = time.time()
        server.resources = {
            "memory_bytes": r.get("memory_bytes"),
            "cpu_percent": r.get("cpu_absolute"),
            "disk_bytes": r.get("disk_bytes"),
            "uptime_ms": r.get("uptime"),
        }
        if state == "ONLINE" and before in ("OFFLINE", "STARTING"):
            await self._tell(server, "rented_started", "success", f"{server.name} is online")
        elif state == "OFFLINE" and before in ("ONLINE", "STOPPING"):
            await self._tell(server, "rented_stopped", "info", f"{server.name} stopped")
        return None

    async def _tell(self, server: RentedServer, kind: str, level: str, message: str) -> None:
        await self.core.bus.publish(
            Event(
                type=kind,
                level=level,
                message=message,
                data={"server_name": server.name, "host": self.panels[server.panel_id]["host"]},
                server_id=server.id,
            )
        )

    async def poll_all(self) -> None:
        slow_down, failed = False, False
        for server in list(self.servers.values()):
            problem = await self.poll_one(server)
            failed |= problem is not None
            slow_down |= problem is not None and problem.status == 429
        if slow_down:
            self.wait = max(self.wait * 2, SLOW_DOWN_WAIT)
        elif failed:
            self.wait = self.wait * 2
        else:
            self.wait = POLL_SECONDS
        self.wait = min(self.wait, MAX_WAIT)

    def start(self) -> None:
        if self.servers and (self._task is None or self._task.done()):
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="rented")

    async def _loop(self) -> None:
        while self.servers:
            await asyncio.sleep(self.wait)
            try:
                await self.poll_all()
            except Exception:
                log.exception("rented server status check failed")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        for server in self.servers.values():
            if server.console:
                server.console.close()

    def console_of(self, server: RentedServer) -> RentedConsole:
        if server.console is None:
            server.console = RentedConsole(self, server)
        return server.console


# The websocket connector; tests swap in a fake panel's.
def _connect(uri: str, origin: str):
    from websockets.asyncio.client import connect

    return connect(
        uri,
        origin=origin,  # type: ignore[arg-type]
        user_agent_header=downloads.USER_AGENT,
        max_size=256 * 1024,
        open_timeout=15,
    )


CONNECT = _connect


class RentedConsole:
    """The live console through the panel's websocket. Open only while
    someone reads it (house rule 7): it closes after CONSOLE_IDLE seconds
    with no reader. Commands go through the REST call, not this socket."""

    def __init__(self, owner: RentedServers, server: RentedServer) -> None:
        self.owner, self.server = owner, server
        self.lines: collections.deque[tuple[int, str]] = collections.deque(maxlen=CONSOLE_LINES)
        self.count = 0
        self.read_at = 0.0
        self.problem: str | None = None
        self._task: asyncio.Task | None = None

    def add(self, line: str) -> None:
        self.count += 1
        self.lines.append((self.count, line[:2000]))

    def read(self, after: int = 0) -> dict[str, Any]:
        self.read_at = time.time()
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run(), name="rented-console")
        return {
            "lines": [{"n": n, "text": t} for n, t in self.lines if n > after],
            "last": self.count,
            "problem": self.problem,
        }

    def close(self) -> None:
        if self._task:
            self._task.cancel()

    async def _token(self) -> tuple[str, str]:
        answer = await self.owner.panel_of(self.server).call(
            "GET", f"/servers/{self.server.identifier}/websocket"
        )
        data = (answer or {}).get("data") or {}
        token, socket = str(data.get("token") or ""), str(data.get("socket") or "")
        parsed = urlparse(socket)
        if not token or parsed.scheme != "wss" or not parsed.hostname or parsed.username:
            raise PanelError("The panel didn't give a secure console address.")
        return token, socket

    async def _run(self) -> None:
        wait = 5
        while time.time() - self.read_at < CONSOLE_IDLE:
            try:
                await self._session()
                wait = 5
            except asyncio.CancelledError:
                raise
            except PanelError as exc:
                self.problem = str(exc)
            except Exception as exc:  # the socket library's own errors
                log.info("rented console connection ended: %s", type(exc).__name__)
                self.problem = "The console connection to the host was lost. Trying again."
            await asyncio.sleep(wait)
            wait = min(wait * 2, 60)

    async def _session(self) -> None:
        token, socket = await self._token()
        address = self.owner.panels[self.server.panel_id]["address"]
        async with CONNECT(socket, address) as ws:
            await ws.send(json.dumps({"event": "auth", "args": [token]}))
            while time.time() - self.read_at < CONSOLE_IDLE:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                except TimeoutError:
                    continue
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                event, args = message.get("event"), message.get("args") or []
                if event == "auth success":
                    self.problem = None
                    await ws.send(json.dumps({"event": "send logs", "args": [None]}))
                elif event == "console output" and args:
                    self.add(str(args[0]))
                elif event == "token expiring":
                    token, _ = await self._token()
                    await ws.send(json.dumps({"event": "auth", "args": [token]}))
                elif event in ("token expired", "jwt error"):
                    raise PanelError("The console sign-in ran out. Reconnecting.")
