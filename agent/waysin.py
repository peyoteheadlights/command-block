"""Diagnose server: the other ways in (home network, Tailscale, the internet,
playit).

The joins from this PC (agent/diagnose.py) already test logging in, so here
only one question is asked per way: does the game port answer from there?
Each line says where it was asked from:

  * this PC, on its own home network and Tailscale addresses. From the same
    PC the Windows Firewall doesn't count, so these prove the server listens
    on that address and no more;
  * the owner's other PC (Your PCs), when there is one: its answer does go
    through the firewall, like a friend's at home would. Only a ping: the
    Microsoft sign-in never leaves this PC;
  * the internet, through mcsrvstat.us, for the address the owner typed for
    /ip. A playit address is listed as playit. No typed address: not checked
    (house rule 1: the internet address is never guessed).

Lines carry ``protocol``, ``port`` and ``outside`` so agent/diagnosefix.py can
see "answers here, not from outside" and offer the firewall rule.
"""

from __future__ import annotations

import asyncio
import ipaddress
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from . import downloads, joininfo, tailscale
from .monitoring import answering

if TYPE_CHECKING:
    from .core import ServerContext
    from .diagnose import Run
    from .jobs import JobHandle

HOME = "Home network"
TAILSCALE = "Tailscale"
INTERNET = "Internet"
PLAYIT = "playit"
NO_ANSWER = "No answer from that address."
NOT_MINECRAFT = "Something took the connection but didn't answer like a Minecraft server."
TAILSCALE_ON = "Tailscale on this PC"
SWITCHED_OFF = "Tailscale is switched off on this PC, so friends can't join through it."
MCSRVSTAT = "https://api.mcsrvstat.us"
# playit's own address names (a Premium custom domain can't be told apart).
PLAYIT_NAMES = ("joinmc.link", "playit.gg", "ply.gg")
# mcsrvstat.us keeps an answer for about a minute. One from before this round
# (before a change was made) is asked again once it runs out, waiting this long at most.
STALE_WAIT = 90.0
# The addresses another PC may be asked to ping: home networks and Tailscale.
TAILSCALE_NET = joininfo.TAILSCALE_NET


@dataclass
class Port:
    protocol: str  # "tcp" (Java) or "raknet" (Bedrock, UDP)
    port: int
    bedrock_players: bool = False  # Geyser's port next to a Java one


def ports(ctx: ServerContext) -> list[Port]:
    port = ctx.core.ports.port_of(ctx).port
    if ctx.config.server_type.edition == "bedrock":
        return [Port("raknet", port)]
    found = [Port("tcp", port)]
    geyser = ctx.core.ports.bedrock_port_of(ctx)
    if geyser:
        found.append(Port("raknet", geyser.port, True))
    return found


def may_ping(host: str) -> bool:
    """Only home network and Tailscale addresses (asked by another PC)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.version == 4 and (ip.is_private or ip in TAILSCALE_NET) and not ip.is_loopback


def answered(found: dict[str, Any]) -> tuple[bool, str | None]:
    if found.get("answer"):
        return True, None
    if found.get("connected"):
        return False, NOT_MINECRAFT
    return False, NO_ANSWER


def is_playit(address: str) -> bool:
    host = address.rsplit(":", 1)[0].lower()
    return any(host == name or host.endswith("." + name) for name in PLAYIT_NAMES)


async def check(out: Run, job: JobHandle) -> None:
    """Every way in this server is shared, one line per place asked from."""
    from .discord import layout

    ctx = out.ctx
    game = ports(ctx)
    peer = _peer(ctx)
    home = next((a["address"] for a in joininfo.local_addresses() if not a["virtual"]), None)
    home_ok = False
    if home:
        home_ok = await _here_and_there(out, job, HOME, home, game, peer)
    status = await asyncio.to_thread(tailscale.tailscale_status)
    if status["cli_found"]:
        address = next((a for a in status["addresses"] if "." in a), None)
        if status["connected"] and address:
            await _here_and_there(out, job, TAILSCALE, address, game, peer)
        elif status["error"]:
            out.line(TAILSCALE, TAILSCALE_ON, None, f"Not checked: {status['error']}")
        elif status["backend_state"] == "Stopped":
            out.line(TAILSCALE, TAILSCALE_ON, False, SWITCHED_OFF)
        else:
            out.line(
                TAILSCALE,
                TAILSCALE_ON,
                False,
                "Tailscale isn't connected on this PC. Click Connect Tailscale in the desktop app.",
            )
    typed = layout.addresses(ctx.core.db).get(ctx.server_id)
    if not typed:
        out.line(
            INTERNET,
            "From the internet",
            None,
            "Not checked: no internet address is set. Type the one friends use in "
            "Discord settings (Address to share), then diagnose again.",
        )
        return
    group = PLAYIT if is_playit(typed) else INTERNET
    first = game[0]
    worked = await _mcsrvstat(out, job, group, typed, first)
    if worked is False and group == INTERNET and home_ok and first.protocol == "tcp":
        out.result["steps"].append(
            f"On your router, forward TCP port {first.port} to {home}. Command Block "
            "can't open router ports yet. Your router's page or manual says how."
        )


def _peer(ctx: ServerContext) -> dict[str, Any] | None:
    """The owner's other PC that asks from outside, when there is one."""
    pcs = ctx.core.pcs
    if not pcs.joined():
        return None
    return next(iter(pcs.peers()), None)


async def _here_and_there(
    out: Run,
    job: JobHandle,
    group: str,
    host: str,
    game: list[Port],
    peer: dict[str, Any] | None,
) -> bool:
    """Ping ``host`` from this PC, then from the other PC. True when this PC
    got an answer on the first port."""
    first_ok = False
    for index, port in enumerate(game):
        where = f"{host}:{port.port}" + (" (Bedrock players)" if port.bedrock_players else "")
        job.step(f"{group}: {where}")
        found = await asyncio.to_thread(answering.ping, port.protocol, host, port.port)
        ok, why = answered(found)
        first_ok = first_ok or (ok and index == 0)
        _line(out, group, f"From this PC: {where}", ok, why, port, outside=False)
        if peer is None:
            continue
        name = str(peer["body"].get("name") or "your other PC")
        try:
            found = await out.ctx.core.pcs.ping_from(peer, port.protocol, host, port.port)
        except Exception as exc:
            _line(
                out,
                group,
                f"From {name}: {where}",
                None,
                f"Not checked: {name} didn't answer ({exc}).",
                port,
                outside=True,
            )
            continue
        ok, why = answered(found)
        _line(out, group, f"From {name}: {where}", ok, why, port, outside=True)
    return first_ok


async def _mcsrvstat(out: Run, job: JobHandle, group: str, address: str, port: Port) -> bool | None:
    what = f"From the internet (mcsrvstat.us): {address}"
    job.step(what)
    edition = "bedrock/" if port.protocol == "raknet" else ""
    url = f"{MCSRVSTAT}/{edition}3/{quote(address, safe=':')}"
    try:
        found = await downloads.fetch_json(url)
        debug = found.get("debug") or {}
        if debug.get("cachehit") and (debug.get("cachetime") or 0) < out.result["started_at"]:
            # Its answer is from before this round: ask again once it runs out.
            wait = (debug.get("cacheexpire") or 0) - time.time() + 1
            await asyncio.sleep(min(max(wait, 0), STALE_WAIT))
            found = await downloads.fetch_json(url)
            if (found.get("debug") or {}).get("cachetime", 0) < out.result["started_at"]:
                _line(
                    out,
                    group,
                    what,
                    None,
                    "Not checked: mcsrvstat.us still had its answer from before. "
                    "Diagnose again in a minute.",
                    port,
                    outside=True,
                )
                return None
    except Exception as exc:
        _line(
            out, group, what, None, f"Not checked: couldn't ask mcsrvstat.us ({exc}).", port, True
        )
        return None
    ok = found.get("online") is True
    _line(out, group, what, ok, None if ok else NO_ANSWER, port, outside=True)
    return ok


def _line(
    out: Run, group: str, what: str, ok: bool | None, why: str | None, port: Port, outside: bool
) -> None:
    out.line(group, what, ok, why)
    out.result["lines"][-1].update(protocol=port.protocol, port=port.port, outside=outside)
