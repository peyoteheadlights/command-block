"""How the desktop app talks to the agent: its API only, like the phone apps.

On a real PC the connection is pinned to the agent's certificate: the app
checks the certificate's SHA-256 against the pairing link's fingerprint
before it sends anything, then trusts that one certificate and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import logging
import socket
import ssl
from pathlib import Path
from typing import Any

import httpx
from websockets.exceptions import InvalidHandshake
from websockets.sync.client import ClientConnection, connect

from agent import appinfo
from agent.pairing import PAIRING_FILE

# websockets logs every frame at debug level, and the first one holds the
# token, so the feed's own logger never goes below info.
FEED_LOG = logging.getLogger("msc.desktop.feed")
FEED_LOG.setLevel(logging.INFO)
# Backups, restores and world imports answer only once done. Waiting stops
# after two hours of silence, so a PC that went to sleep doesn't hold on forever.
SLOW = httpx.Timeout(15, read=7200, write=120)
STILL_WORKING = "The PC hasn't answered for a long time. It may still be working on it."
CHUNK = 1024 * 1024


class AgentError(Exception):
    """The agent said no, in words a person can read."""

    problems: dict[str, str] = {}  # a form's reasons, by field, when the agent gave them


class Unreachable(AgentError):
    pass


class SignedOut(AgentError):
    pass


class WrongCertificate(AgentError):
    pass


def local_pairing() -> dict[str, Any] | None:
    """The pairing file the agent on this PC writes, or None without one."""
    try:
        return json.loads((appinfo.settings_dir() / PAIRING_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def pinned_context(host: str, port: int, fingerprint: str) -> ssl.SSLContext:
    """A TLS context that trusts only the certificate with this fingerprint."""
    probe = ssl.create_default_context()
    probe.check_hostname = False
    probe.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=5) as raw:
            with probe.wrap_socket(raw, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except OSError as exc:
        raise Unreachable(f"Can't reach {host}:{port}.") from exc
    if not der or hashlib.sha256(der).hexdigest() != fingerprint.lower():
        raise WrongCertificate(
            "This PC's certificate isn't the one in the pairing link, so Command Block "
            "won't connect. Get a new pairing link from the PC."
        )
    context = ssl.create_default_context(cadata=ssl.DER_cert_to_PEM_cert(der))
    context.check_hostname = False
    # The pinned certificate is the only trust anchor; it is checked exactly.
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


class AgentClient:
    def __init__(self, http: httpx.Client | None, place: str = "this PC") -> None:
        self.http = http  # None until the agent could be reached (see connect)
        self.place = place  # which PC, for its saved sign-in
        self.token: str | None = None
        self.ssl: ssl.SSLContext | None = None  # the pinned certificate, for the live feed
        self.address: tuple[str, int, str, bool] | None = None

    @classmethod
    def connect(cls, host: str, port: int, fingerprint: str | None, tls: bool = True):
        """A client for the agent at host:port. If the agent isn't running
        yet, the certificate is checked on the first request instead, so the
        window can open and say so."""
        client = cls(None, "this PC" if host == "127.0.0.1" else host)
        client.address = (host, port, fingerprint or "", tls)
        try:
            client.reach()
        except Unreachable:
            pass
        return client

    def reach(self) -> httpx.Client:
        if self.http is None:
            assert self.address is not None
            host, port, fingerprint, tls = self.address
            if not tls:
                self.http = httpx.Client(base_url=f"http://{host}:{port}", timeout=15)
            else:
                self.ssl = pinned_context(host, port, fingerprint)
                self.http = httpx.Client(
                    base_url=f"https://{host}:{port}", verify=self.ssl, timeout=15
                )
        return self.http

    def open_feed(self) -> ClientConnection:
        """The live feed (the agent's /ws), signed in. Read it with recv()."""
        url = str(self.reach().base_url.join("/ws")).replace("http", "ws", 1)
        try:
            ws = connect(url, ssl=self.ssl, open_timeout=10, logger=FEED_LOG)
        except (OSError, InvalidHandshake) as exc:
            raise Unreachable("Can't reach the agent.") from exc
        ws.send(json.dumps({"type": "auth", "token": self.token}))
        return ws

    def call(self, method: str, path: str, body: dict | None = None, slow: bool = False) -> Any:
        """``slow`` for work the agent answers only once it is done (a
        backup, a restore), which can take many minutes on a big world."""
        return self.request(method, path, body, slow).json()

    def download(self, path: str) -> bytes:
        """A small file the agent made, like the Get help zip."""
        return self.request("GET", path).content

    def download_to(self, path: str, target: str) -> None:
        """A file that can be big (a backup), written to ``target`` as it
        comes so it never sits in memory. A part-written file is removed."""
        written = False
        try:
            with self.reach().stream(
                "GET", "/api" + path, headers=self.headers(), timeout=SLOW
            ) as response:
                self.check(response, streamed=True)
                with open(target, "wb") as out:
                    written = True
                    for chunk in response.iter_bytes(CHUNK):
                        out.write(chunk)
        except BaseException as exc:
            if written:  # a file the save box said to replace stays until then
                Path(target).unlink(missing_ok=True)
            if isinstance(exc, httpx.TransportError):
                raise Unreachable("The download stopped: can't reach the agent.") from exc
            if isinstance(exc, OSError):
                raise AgentError(f"The file couldn't be saved: {exc.strerror or exc}") from exc
            raise

    def upload(self, path: str, file: str) -> Any:
        """Sends a file (a world zip) to a route that takes one; httpx reads
        it in pieces, so a big world isn't read into memory first."""
        with open(file, "rb") as data:
            return self.request("POST", path, slow=True, files={"file": data}).json()

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        slow: bool = False,
        files: dict | None = None,
    ) -> httpx.Response:
        try:
            response = self.reach().request(
                method,
                "/api" + path,
                json=body,
                files=files,
                headers=self.headers(),
                timeout=SLOW if slow else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.ReadTimeout as exc:
            if slow:
                raise AgentError(STILL_WORKING) from exc
            raise Unreachable("Can't reach the agent.") from exc
        except httpx.TransportError as exc:
            raise Unreachable("Can't reach the agent.") from exc
        self.check(response)
        return response

    def check(self, response: httpx.Response, streamed: bool = False) -> None:
        if response.status_code >= 400:
            if streamed:
                response.read()
            try:
                body = response.json()
            except ValueError:
                body = None
            detail = body.get("detail") if isinstance(body, dict) else None
            message = (
                detail if isinstance(detail, str) else f"The agent said {response.status_code}."
            )
            if response.status_code == 401:
                self.token = None
                raise SignedOut(message)
            error = AgentError(message)
            found = body.get("problems") if isinstance(body, dict) else None
            error.problems = found if isinstance(found, dict) else {}
            raise error

    def sign_in(self, username: str, password: str, remember: bool, device: str) -> dict:
        result = self.call(
            "POST",
            "/auth/login",
            {
                "username": username,
                "password": password,
                "remember": remember,
                "device": device,
                "app": True,
            },
        )
        self.token = result["token"]
        return result

    def sign_in_once(self, code: str, device: str) -> dict:
        """The installer's one-time code: signs the owner in, kept until
        signed out, from the agent's own PC only."""
        result = self.call("POST", "/auth/once", {"code": code, "device": device})
        self.token = result["token"]
        return result

    def check_invite(self, token: str) -> dict:
        """Who an invite is for, before choosing a password."""
        return self.call("POST", "/invite/check", {"token": token})

    def accept_invite(self, token: str, password: str, device: str) -> dict:
        """Choose the invite's password; signs in, kept until signed out."""
        result = self.call(
            "POST", "/invite/accept", {"token": token, "password": password, "device": device}
        )
        self.token = result["token"]
        return result

    def sign_out(self) -> None:
        try:
            self.call("POST", "/auth/logout")
        finally:
            self.token = None

    def on(
        self, server_id: str, method: str, path: str, body: dict | None = None, slow: bool = False
    ) -> Any:
        """Any route of one server, for the pages: ``on(sid, "GET", "/chat")``."""
        return self.call(method, f"/servers/{server_id}{path}", body, slow)

    def servers(self) -> list[dict]:
        return self.call("GET", "/servers")["servers"]

    def server_action(self, server_id: str, action: str) -> dict:
        return self.call("POST", f"/servers/{server_id}/server/{action}", {})

    def preferences(self) -> dict:
        return self.call("GET", "/account/preferences")

    def set_mode(self, mode: str) -> dict:
        return self.call("PUT", "/account/preferences", {"mode": mode})

    def changes(self, server_id: str) -> dict:
        return self.call("GET", f"/servers/{server_id}/changes")

    def move_change(self, server_id: str, which: str, change_id: int, start_after: bool) -> dict:
        """Undo or Redo the newest change, only if it is still ``change_id``;
        the server is started again after when start_after."""
        return self.call(
            "POST",
            f"/servers/{server_id}/changes/{which}",
            {"start_after": start_after, "change_id": change_id},
            slow=True,
        )

    def players(self, server_id: str) -> dict:
        return self.call("GET", f"/servers/{server_id}/players")

    def schedules(self, server_id: str) -> list[dict]:
        return self.call("GET", f"/servers/{server_id}/schedules")["schedules"]

    def suggestions(self, server_id: str) -> dict:
        return self.call("GET", f"/servers/{server_id}/recommendations")

    def suggestion(self, server_id: str, rec_id: str, verb: str) -> dict:
        """Snooze or dismiss one; returns the suggestions left."""
        return self.call("POST", f"/servers/{server_id}/recommendations/{rec_id}", {"action": verb})

    def checklist(self, server_id: str) -> dict:
        return self.call("GET", f"/servers/{server_id}/getting-started")

    def dismiss_checklist(self, server_id: str) -> dict:
        return self.call("POST", f"/servers/{server_id}/getting-started/dismiss", {})

    def support_ask(self) -> dict:
        return self.call("GET", "/support-ask")

    def answer_support_ask(self, answer: str) -> dict:
        return self.call("POST", "/support-ask", {"answer": answer})

    def join(self, server_id: str) -> dict:
        return self.call("GET", f"/servers/{server_id}/join")

    def backups(self, server_id: str) -> dict:
        return self.call("GET", f"/servers/{server_id}/backups")

    def back_up(self, server_id: str) -> dict:
        return self.call("POST", f"/servers/{server_id}/backups", {}, slow=True)

    def mods(self, server_id: str) -> dict:
        return self.call("GET", f"/servers/{server_id}/mods")

    def addons(self, server_id: str) -> dict:
        """A Bedrock server's packs, which it has instead of mods."""
        return self.call("GET", f"/servers/{server_id}/addons")

    def performance(self, server_id: str) -> dict:
        return self.call("GET", f"/servers/{server_id}/performance?hours=1&storage=false")

    def status(self, server_id: str) -> dict:
        return self.call("GET", f"/servers/{server_id}/status")

    def logs(self, server_id: str, lines: int = 12) -> dict:
        return self.call("GET", f"/servers/{server_id}/logs?lines={lines}")

    def health(self) -> dict:
        """Needs no sign-in: is the agent running, and which API version."""
        return self.call("GET", "/health")

    def version(self) -> dict:
        return self.call("GET", "/version")

    def me(self) -> dict:
        """Who is signed in, and what they may do on each server."""
        return self.call("GET", "/auth/me")

    def alerts(self, after: int = 0) -> dict:
        """Alerts newer than the id ``after``, oldest first (0: the newest 200)."""
        return self.call("GET", f"/alerts?after={after}&limit=200")

    def mark_alerts_read(self, up_to: int) -> dict:
        """This account has read every alert up to the id ``up_to``."""
        return self.call("POST", "/alerts/read", {"up_to": up_to})

    def events(self, server_id: str, limit: int = 20) -> list[dict]:
        return self.call("GET", f"/servers/{server_id}/events?limit={limit}")["events"]

    def tailscale(self) -> dict:
        """The Tailscale step's state (owner only)."""
        return self.call("GET", "/tailscale")

    def tailscale_move(self) -> dict:
        """Ask the agent to move onto Tailscale once Tailscale says connected."""
        return self.call("POST", "/tailscale/move", {})

    def tailscale_skip(self) -> dict:
        return self.call("POST", "/tailscale/skip", {})

    def pairing(self) -> dict:
        """The phone pairing link and its QR rows, or why there isn't one."""
        return self.call("GET", "/pairing")
