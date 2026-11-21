"""playit.gg built in: friends join through playit's tunnel, so no router
port is opened and the home address stays hidden.

playit's own program (the Windows service ``playitd``) does the tunnel. The
desktop app installs it after a click (desktop/pc.py, its signed setup). The
agent then only talks to that program and to playit's sign-in:

  * the link (claim): the agent makes a claim code and shows playit's page
    for it. Once the owner approved it there, the agent gets the program's
    key from playit and hands it straight to the program over its local
    pipe. The key is never saved or logged here; playit's program keeps it
  * the tunnel is made by the owner on playit.gg (playit has no documented
    way to make one from an app). The agent reads the tunnels the program
    reports and matches them to a server by its port
  * a tunnel's address fills in the server's shared address (invites, QR
    codes, Discord) unless the owner typed one of their own
  * every minute while in use: if the program stops, one alert, and
    Reconnect runs ``playit start``
  * "Stop using playit.gg" unlinks the program (its key is removed), so the
    tunnels stop. The desktop app then offers to uninstall it

How the program is spoken to was read from its source (playit-agent 2.0.2,
BSD-2-Clause): JSON lines on ``\\\\.\\pipe\\playitd-system``, which any
signed-in Windows user may use. research/playit.md has the details.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

from . import __version__, downloads, internet, waysin
from .discord import layout
from .events import Event
from .winproc import NO_WINDOW

if TYPE_CHECKING:
    from .core import AgentCore, ServerContext

log = logging.getLogger("msc.playit")

PIPE = r"\\.\pipe\playitd-system"
# The 64-bit Program Files folder, even if this Python were 32-bit.
PROGRAM_FILES = os.environ.get("ProgramW6432") or os.environ.get(
    "ProgramFiles", r"C:\Program Files"
)
PROGRAM = Path(PROGRAM_FILES) / "playit_gg" / "bin" / "playit.exe"
API = "https://api.playit.gg"
SITE = "https://playit.gg"
CLAIM_PAGE = SITE + "/claim/{code}"
NEW_TUNNEL = SITE + "/account/setup/new-tunnel"
SETTING = "playit"  # {"on": bool, "addresses": {server_id: address it filled in}}
CHECK_EVERY = 60
CLAIM_FOR = 15 * 60  # the owner has this long to approve on playit.gg
ANSWER_WAIT = 10.0  # the program answers its pipe at once; longer means it hangs


class PlayitError(RuntimeError):
    """Said to the owner as it is."""


# ---------------------------------------------------------------- the program's pipe
def _lines(pipe: BinaryIO):
    """The pipe's JSON lines, one at a time (it is read unbuffered)."""
    kept = b""
    while True:
        while b"\n" not in kept:
            more = pipe.read(65536)
            if not more:
                raise OSError("playit closed the pipe")
            kept += more
        line, kept = kept.split(b"\n", 1)
        if line.strip():
            yield json.loads(line)


def talk(pipe: BinaryIO, request: dict[str, Any]) -> dict[str, Any]:
    """One request, its answer (``{"type": ..., "data": ...}``). The program
    says hello first, with its protocol version, which the request repeats."""
    read = _lines(pipe)
    hello = next(read)
    if hello.get("message_kind") != "hello":
        raise OSError("playit didn't say hello")
    version = hello["data"]["protocol"]["ipc_version"]
    envelope = {"ipc_version": version, "request_id": 1, "request": request}
    pipe.write(json.dumps(envelope).encode() + b"\n")
    for message in read:  # events in between (logs, stats) are skipped
        if message.get("message_kind") == "response":
            return message["data"]["response"]
    raise OSError("no answer")  # pragma: no cover (the reader raises first)


def ask(request: dict[str, Any]) -> dict[str, Any]:
    """Raises OSError when the program isn't running (no pipe)."""
    with open(PIPE, "r+b", buffering=0) as pipe:
        return talk(pipe, request)


def installed() -> bool:
    return PROGRAM.is_file()


def read_program() -> dict[str, Any]:
    """What playit's program says now, never guessed: ``phase`` is one of
    not_installed, stopped (installed, not answering), waiting_for_secret
    (not linked), has_invalid_secret, disabled_over_limit, starting,
    running, stopping, error."""
    try:
        answer = ask({"type": "get_state"})
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        phase = "stopped" if installed() else "not_installed"
        return {"phase": phase, "problem": None, "tunnels": [], "login_link": None, "why": str(exc)}
    if answer.get("type") == "error":
        problem = (answer.get("data") or {}).get("message")
        return {"phase": "error", "problem": problem, "tunnels": [], "login_link": None}
    life = answer.get("data") or {}
    data = life.get("data") or {}
    running = life.get("state") == "running"
    return {
        "phase": life.get("state") or "error",
        # The other states carry playit's own words for what's wrong.
        "problem": None if running else (data.get("message") if isinstance(data, dict) else None),
        "tunnels": [
            {
                "address": t.get("display_address") or "",
                "destination": t.get("destination") or "",
                "disabled": bool(t.get("is_disabled")),
                "reason": t.get("disabled_reason"),
            }
            for t in (data.get("tunnels") or [] if running else [])
        ],
        # A guest account (no playit sign-in yet) gets a link that opens it.
        "login_link": data.get("login_link") if running else None,
    }


def local_port(destination: str) -> int | None:
    """ "127.0.0.1:25565" -> 25565. HTTPS tunnels and the like: None."""
    try:
        return int(destination.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


# ---------------------------------------------------------------- playit's sign-in
async def _api(path: str, body: dict[str, Any]) -> Any:
    found = await downloads.fetch_json(
        API + path,
        max_bytes=64 * 1024,
        method="POST",
        content=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    if not isinstance(found, dict) or found.get("status") != "success":
        said = found.get("data") if isinstance(found, dict) else None
        raise PlayitError(f"playit.gg said no ({str(said)[:80]}).")
    return found.get("data")


class Playit:
    """This PC's playit.gg: one program, its tunnels shared by the servers."""

    def __init__(self, core: AgentCore) -> None:
        self.core = core
        self.claim: dict[str, Any] | None = None  # {"url", "state", "detail"}
        self.down = False
        self.last: dict[str, Any] = {"phase": "not_installed", "tunnels": []}
        self._claiming: asyncio.Task | None = None
        self._task: asyncio.Task | None = None

    def saved(self) -> dict[str, Any]:
        return self.core.db.get_setting(SETTING, {}) or {}

    def _save(self, **changes: Any) -> None:
        self.core.db.set_setting(SETTING, {**self.saved(), **changes})

    @property
    def on(self) -> bool:
        return bool(self.saved().get("on"))

    async def refresh(self) -> dict[str, Any]:
        self.last = await asyncio.wait_for(asyncio.to_thread(read_program), ANSWER_WAIT)
        if self.on:
            self.fill_addresses()
        return self.last

    # ------------------------------------------------------------ one server's part
    def ways(self, ctx: ServerContext) -> list[dict[str, Any]]:
        """The tunnels this server needs: Java on its port, Bedrock on
        Geyser's. A plain Bedrock server goes through WaterdogPE (later)."""
        out = []
        for protocol, port in internet.router_ports(ctx):
            kind = "java" if protocol == "TCP" else "bedrock"
            tunnel = next(
                (t for t in self.last.get("tunnels") or [] if local_port(t["destination"]) == port),
                None,
            )
            out.append({"kind": kind, "local_port": port, "tunnel": tunnel})
        return out

    def tunnel_address(self, ctx: ServerContext) -> str | None:
        java = next((w for w in self.ways(ctx) if w["kind"] == "java"), None)
        tunnel = java and java["tunnel"]
        return tunnel["address"] if tunnel and not tunnel["disabled"] else None

    def summary(self, ctx: ServerContext) -> dict[str, Any]:
        """GET /servers/{id}/playit. Never holds the program's key."""
        bedrock = ctx.config.server_type.edition == "bedrock"
        test = internet.state(ctx).playit_test
        return {
            "on": self.on,
            "phase": self.last.get("phase"),
            "problem": self.last.get("problem"),
            "down": self.down,
            "claim": self.claim,
            "ways": [] if bedrock else self.ways(ctx),
            "supported": not bedrock,
            # Join protection reads the player's own address from the PROXY header.
            "proxy": "v2" if ctx.config.server.join_protection else "none",
            "new_tunnel": self.last.get("login_link") or NEW_TUNNEL,
            "site": SITE,
            "test": test,
            "mcsrvstat_ok": bool(ctx.core.db.get_setting(internet.MCSRVSTAT_OK, False)),
        }

    # ------------------------------------------------------------ the owner's buttons
    async def use(self) -> None:
        """ "Use playit.gg", after the desktop app installed the program.
        Links it when it has no key yet; a program linked before (its key
        survives a reinstall) just carries on."""
        found = await self.refresh()
        if found["phase"] == "not_installed":
            raise PlayitError("playit.gg isn't installed on this PC yet.")
        self._save(on=True)
        self.down = False
        if found["phase"] == "waiting_for_secret" and not (
            self._claiming and not self._claiming.done()
        ):
            code = secrets.token_hex(5)  # what playit's own program makes
            self.claim = {"url": CLAIM_PAGE.format(code=code), "state": "waiting", "detail": None}
            self._claiming = asyncio.create_task(self._link(code), name="playit-link")
        self.ensure_running()

    async def _link(self, code: str) -> None:
        claim = self.claim or {}
        try:
            until = time.time() + CLAIM_FOR
            while True:
                answer = await _api(
                    "/claim/setup",
                    {
                        "code": code,
                        "agent_type": "assignable",
                        "version": f"Command Block {__version__}",
                    },
                )
                if answer == "UserAccepted":
                    break
                if answer == "UserRejected":
                    claim.update(state="rejected")
                    return
                if time.time() > until:
                    claim.update(state="expired")
                    return
                await asyncio.sleep(2)
            key = (await _api("/claim/exchange", {"code": code}))["secret_key"]
            said = await asyncio.wait_for(
                asyncio.to_thread(ask, {"type": "set_secret", "secret": key}), ANSWER_WAIT
            )
            if said.get("type") == "error":
                raise PlayitError((said.get("data") or {}).get("message") or "playit said no")
            self.claim = None
            await self.refresh()
        except (
            PlayitError,
            downloads.DownloadError,
            OSError,
            KeyError,
            TypeError,
            TimeoutError,
        ) as exc:
            claim.update(state="failed", detail=str(exc))  # never holds the key

    async def reconnect(self) -> None:
        """Starts playit's program again: its own ``playit start``."""
        if not installed():
            raise PlayitError("playit.gg isn't installed on this PC.")

        def start() -> subprocess.CompletedProcess:
            return subprocess.run(
                [str(PROGRAM), "start"],
                capture_output=True,
                text=True,
                timeout=60,
                stdin=subprocess.DEVNULL,
                creationflags=NO_WINDOW,
            )

        try:
            done = await asyncio.to_thread(start)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PlayitError(f"playit didn't start: {exc}") from exc
        if done.returncode != 0:
            raise PlayitError((done.stderr or done.stdout or "playit didn't start").strip()[:300])
        self.down = False
        await self.refresh()

    async def stop(self) -> str | None:
        """Unlinks the program (its key removed, so the tunnels stop) and
        takes back the addresses it filled in. Returns a problem in words
        when the program couldn't be asked (it may not be running)."""
        if self._claiming:
            self._claiming.cancel()
        self.claim, self.down = None, False
        problem = None
        try:
            said = await asyncio.wait_for(
                asyncio.to_thread(ask, {"type": "reset_secret"}), ANSWER_WAIT
            )
            if said.get("type") == "error":
                problem = (said.get("data") or {}).get("message")
        except (OSError, ValueError, TimeoutError) as exc:
            problem = f"playit didn't answer ({exc})"
        shared = layout.addresses(self.core.db)
        for server_id, address in (self.saved().get("addresses") or {}).items():
            if shared.get(server_id) == address:
                layout.set_address(self.core.db, server_id, None)
        self._save(on=False, addresses={})
        await self.refresh()
        return problem

    # ------------------------------------------------------------ shared address
    def fill_addresses(self) -> None:
        """A server's tunnel address becomes its shared address (invites, QR
        codes, Discord, Diagnose server), unless the owner typed another."""
        filled = dict(self.saved().get("addresses") or {})
        shared = layout.addresses(self.core.db)
        changed = False
        for ctx in self.core.servers.values():
            address = self.tunnel_address(ctx)
            mine = filled.get(ctx.server_id)
            if not address or shared.get(ctx.server_id) not in (None, "", mine):
                continue
            if shared.get(ctx.server_id) != address:
                layout.set_address(self.core.db, ctx.server_id, address)
            if mine != address:
                filled[ctx.server_id], changed = address, True
        if changed:
            self._save(addresses=filled)

    # ------------------------------------------------------------ the watch
    async def tick(self) -> None:
        if not self.on:
            return
        found = await self.refresh()
        if found["phase"] == "running":
            self.down = False
        elif found["phase"] != "waiting_for_secret" and not self.down:
            # Waiting for its key is the link step, not a stop.
            self.down = True
            await self.core.bus.publish(
                Event(
                    type="playit_stopped",
                    level="error",
                    message="Friends can't join through playit.gg: it stopped on this PC",
                    data={"phase": found["phase"], "problem": found.get("problem")},
                )
            )

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("playit check failed")
            await asyncio.sleep(CHECK_EVERY)

    def start(self) -> None:
        if self.on:
            self.ensure_running()

    def ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="playit-watch")

    def stop_watch(self) -> None:
        if self._task:
            self._task.cancel()
        if self._claiming:
            self._claiming.cancel()


async def test_tunnel(ctx: ServerContext, address: str) -> dict[str, Any]:
    """The outside test through mcsrvstat.us on the tunnel's address."""
    found = internet.state(ctx)
    try:
        works: bool | None = await waysin.ask_mcsrvstat(address, False, found.since)
        reason = None
    except Exception as exc:  # a stale answer or mcsrvstat.us down: not known
        works, reason = None, f"not_checked: {exc}"[:200]
    found.playit_test = {
        "works": works,
        "by": "mcsrvstat",
        "at": time.time(),
        "address": address,
        "reason": reason,
    }
    return found.playit_test
