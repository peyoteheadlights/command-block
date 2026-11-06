"""A fake Pterodactyl panel, like the fake Minecraft server: its client API
through the downloader's swappable transport, and its console websocket
through agent.rented.CONNECT. No network."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx

HOST = "panel.example.com"
ADDRESS = f"https://{HOST}"
KEY = "ptlc_testkey_abcdefghijklmnop"


class FakePanel:
    def __init__(self) -> None:
        self.key = KEY
        self.servers = {
            "abcd1234": {"name": "Survival", "state": "offline", "owner": False},
            "ef567890": {"name": "Creative", "state": "running", "owner": False},
        }
        self.down = False
        self.status: int | None = None  # answer every call with this code
        self.refused: set[str] = set()  # paths answered 403
        self.calls: list[tuple[str, str, dict | None]] = []
        self.backups: list[dict] = [
            {
                "uuid": "b-1",
                "name": "Before the update",
                "bytes": 1234,
                "created_at": "2026-11-01T10:00:00+00:00",
                "completed_at": "2026-11-01T10:01:00+00:00",
                "is_successful": True,
                "is_locked": False,
            }
        ]
        self.console = ["[Server] Done (3.2s)!", "[Server] Steve joined the game"]
        self.sockets: list[FakeSocket] = []

    def answer(self, code: int, body: dict | None = None) -> httpx.Response:
        return httpx.Response(code, json=body) if body is not None else httpx.Response(code)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("the panel is down")
        if request.url.host != HOST:
            return httpx.Response(404)
        if request.headers.get("authorization") != f"Bearer {self.key}":
            return self.answer(401, {"errors": [{"code": "AuthenticationException"}]})
        body = json.loads(request.content) if request.content else None
        path = request.url.path.removeprefix("/api/client")
        self.calls.append((request.method, path, body))
        if self.status:
            return self.answer(self.status, {"errors": []})
        if path in self.refused:
            return self.answer(403, {"errors": []})
        if path == "":
            data = [
                {
                    "object": "server",
                    "attributes": {
                        "identifier": i,
                        "name": s["name"],
                        "node": "Node 1",
                        "server_owner": s["owner"],
                    },
                }
                for i, s in self.servers.items()
            ]
            meta = {"pagination": {"total_pages": 1}}
            return self.answer(200, {"object": "list", "data": data, "meta": meta})
        parts = path.strip("/").split("/")
        if len(parts) < 3 or parts[0] != "servers" or parts[1] not in self.servers:
            return self.answer(404, {"errors": []})
        server, what = self.servers[parts[1]], parts[2]
        if what == "resources" and request.method == "GET":
            return self.answer(
                200,
                {
                    "object": "stats",
                    "attributes": {
                        "current_state": server["state"],
                        "is_suspended": False,
                        "resources": {
                            "memory_bytes": 1_500_000_000,
                            "cpu_absolute": 12.5,
                            "disk_bytes": 900_000_000,
                            "uptime": 60_000,
                        },
                    },
                },
            )
        if what == "power" and request.method == "POST":
            signal = (body or {}).get("signal")
            server["state"] = {"start": "starting", "stop": "stopping", "kill": "offline"}.get(
                signal, server["state"]
            )
            return self.answer(204)
        if what == "command" and request.method == "POST":
            if server["state"] != "running":
                return self.answer(502, {"errors": []})
            self.console.append(f"> {(body or {}).get('command')}")
            return self.answer(204)
        if what == "backups" and request.method == "GET":
            data = [{"object": "backup", "attributes": b} for b in self.backups]
            return self.answer(200, {"object": "list", "data": data, "meta": {"backup_limit": 3}})
        if what == "backups" and request.method == "POST":
            self.backups.append(
                {
                    "uuid": f"b-{len(self.backups) + 1}",
                    "name": "Made in Command Block",
                    "bytes": 0,
                    "created_at": "2026-11-06T10:00:00+00:00",
                    "completed_at": None,
                    "is_successful": False,
                    "is_locked": False,
                }
            )
            return self.answer(200, {"object": "backup", "attributes": self.backups[-1]})
        if what == "websocket" and request.method == "GET":
            return self.answer(
                200,
                {"data": {"token": "jwt-1", "socket": f"wss://node.{HOST}:8080/api/servers/x/ws"}},
            )
        return self.answer(405, {"errors": []})

    @asynccontextmanager
    async def connect(self, uri: str, origin: str):
        socket = FakeSocket(self, uri, origin)
        self.sockets.append(socket)
        yield socket


class FakeSocket:
    def __init__(self, panel: FakePanel, uri: str, origin: str) -> None:
        self.panel, self.uri, self.origin = panel, uri, origin
        self.sent: list[dict] = []
        self.queue: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        if message["event"] == "auth" and message["args"] == ["jwt-1"]:
            await self.queue.put(json.dumps({"event": "auth success"}))
        elif message["event"] == "send logs":
            for line in self.panel.console:
                await self.queue.put(json.dumps({"event": "console output", "args": [line]}))

    async def recv(self) -> str:
        return await self.queue.get()
