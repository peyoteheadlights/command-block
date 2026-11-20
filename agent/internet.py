""" "Let friends join from the internet": the router port by UPnP while the
server runs, the internet address the router reports, and the outside test.

  * on (``server.internet``): the agent asks the router to send the game
    ports to this PC when the server starts, and to stop when it stops or
    the owner turns it off (agent/protection.py's router hooks)
  * the internet address is the one the router says. A shared address
    (carrier-grade NAT) or a private one (another router in front) can't
    be reached from outside, so the card offers playit.gg instead
  * the outside test is a real check (house rule 1): the phone app over
    mobile data, or mcsrvstat.us once the owner said yes to it (PRIVACY.md).
    Until a test passes, the card keeps saying "not known"
  * when the router can't, the card shows the port-forwarding steps with
    this PC's home address and the ports

Only game ports are ever opened, never the agent's own port.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import upnp, waysin
from .ports import game_port

if TYPE_CHECKING:
    from .core import ServerContext

log = logging.getLogger(__name__)

# PC-wide: the owner said yes to sending the internet address to mcsrvstat.us.
MCSRVSTAT_OK = "internet_test_mcsrvstat_ok"
PLAYIT = "https://playit.gg"


@dataclass
class State:
    """One server's router port and last outside test, while the agent runs."""

    opened: upnp.Opened | None = None
    problem: str | None = None  # no_router, refused, shared, private
    detail: str = ""
    test: dict[str, Any] | None = None
    busy: bool = False
    since: float = field(default_factory=time.time)


_states: dict[str, State] = {}


def state(ctx: ServerContext) -> State:
    return _states.setdefault(ctx.server_id, State())


def router_ports(ctx: ServerContext) -> list[tuple[str, int]]:
    """The game ports friends use: the server's own port (Join protection's
    guard takes that same one), plus Geyser's for Bedrock players. A Bedrock
    server needs its UDP port and NetherNet's TCP join page on the same number."""
    kind = ctx.config.server_type
    port = game_port(ctx.config.server_dir, None, kind.game_protocol, kind.default_port).port
    if kind.edition == "bedrock":
        return [("UDP", port), ("TCP", port)]
    found = [("TCP", port)]
    geyser = ctx.core.ports.bedrock_port_of(ctx)
    if geyser:
        found.append(("UDP", geyser.port))
    return found


def is_on(ctx: ServerContext) -> bool:
    return bool(ctx.config.server.internet)


# ---------------------------------------------------------------- the router hooks
async def open_port(ctx: ServerContext) -> bool:
    """On start (and when turned on while running). True when the router
    holds every port and its address can be reached from outside."""
    if not is_on(ctx):
        return False
    found = state(ctx)
    found.busy = True
    found.problem, found.detail, found.test = None, "", None
    try:
        opened = await upnp.open_ports(router_ports(ctx), f"Command Block {ctx.name}"[:60])
    except upnp.UpnpError as exc:
        found.opened, found.problem, found.detail = None, exc.reason, str(exc)
        return False
    except Exception as exc:  # the library or network failed in a way we didn't expect
        log.exception("UPnP failed for %s", ctx.server_id)
        found.opened, found.problem, found.detail = None, "refused", str(exc)
        return False
    finally:
        found.busy = False
    found.opened, found.since = opened, time.time()
    kind = upnp.address_kind(opened.public_address)
    if kind in ("shared", "private"):
        found.problem = kind
        return False
    return kind == "public"


async def close_port(ctx: ServerContext) -> None:
    found = state(ctx)
    if found.opened is None:
        return
    ports, found.opened, found.test = found.opened.ports, None, None
    await upnp.close_ports(ports)


async def outside_test(ctx: ServerContext) -> None:
    """After each open: mcsrvstat.us, only if the owner said yes once."""
    if ctx.core.db.get_setting(MCSRVSTAT_OK, False):
        await test_with_mcsrvstat(ctx)


def mapped(ctx: ServerContext) -> bool | None:
    """For the Protection card: None when off (a port forwarded by hand can't be seen)."""
    if not is_on(ctx):
        return None
    return state(ctx).opened is not None


# ---------------------------------------------------------------- the outside test
def public_address(ctx: ServerContext) -> str | None:
    opened = state(ctx).opened
    if opened is None or upnp.address_kind(opened.public_address) != "public":
        return None
    return opened.public_address


async def test_with_mcsrvstat(ctx: ServerContext) -> dict[str, Any]:
    address = public_address(ctx)
    if not address:
        return _keep(ctx, None, "mcsrvstat", "no_address")
    protocol, port = router_ports(ctx)[0]
    bedrock = ctx.config.server_type.edition == "bedrock"
    worked = await waysin.ask_mcsrvstat(f"{address}:{port}", bedrock, state(ctx).since)
    return _keep(ctx, worked, "mcsrvstat", None if worked is not None else "not_checked")


def phone_test(ctx: ServerContext, address: str, port: int, works: bool) -> dict[str, Any]:
    """The phone app pinged over mobile data. Only kept for the address and
    port the card shows right now."""
    expected = public_address(ctx)
    if address != expected or port != router_ports(ctx)[0][1]:
        return _keep(ctx, None, "phone", "address_changed")
    return _keep(ctx, works, "phone", None)


def _keep(ctx: ServerContext, works: bool | None, by: str, reason: str | None) -> dict[str, Any]:
    test = {"works": works, "by": by, "at": time.time(), "reason": reason}
    state(ctx).test = test
    return test


# ---------------------------------------------------------------- what the card shows
def summary(ctx: ServerContext) -> dict[str, Any]:
    """The "internet" part of GET /join and of the Server address section."""
    found = state(ctx)
    opened = found.opened
    port = router_ports(ctx)[0][1]
    test = found.test or {}
    known = test.get("works") is not None
    out: dict[str, Any] = {
        "on": is_on(ctx),
        "busy": found.busy,
        "running": ctx.server.running,
        "known": known,
        "works": test.get("works") if known else None,
        "tested_by": test.get("by") if known else None,
        "tested_at": test.get("at") if known else None,
        "reason": _reason(ctx, found, test),
        "address": public_address(ctx),
        "router_address": opened.public_address if opened else None,
        "port": port,
        "ports": [{"protocol": p, "port": n} for p, n in router_ports(ctx)],
        "local_address": opened.local_address if opened else _home_address(),
        "router": opened.router if opened else None,
        "problem": found.problem,
        "detail": found.detail,
        "mcsrvstat_ok": bool(ctx.core.db.get_setting(MCSRVSTAT_OK, False)),
        "playit": PLAYIT if found.problem in ("shared", "private") else None,
        "names": ctx.core.ddns.names(),
    }
    return out


def _reason(ctx: ServerContext, found: State, test: dict[str, Any]) -> str:
    if test.get("works") is not None:
        return "tested"
    if not is_on(ctx):
        return "off"
    if not ctx.server.running:
        return "not_running"
    if found.problem:
        return found.problem
    if found.opened is None:
        return "not_open"
    return test.get("reason") or "not_tested"


def _home_address() -> str | None:
    from .joininfo import local_addresses

    return next((a["address"] for a in local_addresses() if not a["virtual"]), None)
