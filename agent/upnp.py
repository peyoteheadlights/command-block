"""Talking to the home router by UPnP: open a port, close it, read the
router's internet address. Only file that uses the UPnP library
(async-upnp-client), so it can be swapped here alone.

Nothing is guessed (house rule 1): a port counts as open only when the
router lists it back, and the internet address is the one the router says.
No program runs; the agent's own port is never opened (only the ports
agent/internet.py asks for, which are game ports).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

SEARCH_SECONDS = 3
ANY_HOST = ipaddress.IPv4Address("0.0.0.0")
# Carrier-grade NAT: the internet provider shares one address between homes.
SHARED_NET = ipaddress.ip_network("100.64.0.0/10")


class UpnpError(RuntimeError):
    """Plain words for the owner; ``reason`` for the apps' texts."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


@dataclass
class Router:
    """A found router: the library's IGD device plus where it lives."""

    device: Any  # async_upnp_client IgdDevice
    host: str  # the router's own address on the home network
    name: str = ""


@dataclass
class Opened:
    public_address: str | None
    local_address: str
    router: str
    ports: list[tuple[str, int]] = field(default_factory=list)


async def find_router() -> Router:
    """The first router on the home network that answers UPnP."""
    from async_upnp_client.aiohttp import AiohttpRequester
    from async_upnp_client.client_factory import UpnpFactory
    from async_upnp_client.profiles.igd import IgdDevice

    found = await IgdDevice.async_search(timeout=SEARCH_SECONDS)
    for answer in found:
        location = answer.get("location")
        if not location:
            continue
        try:
            factory = UpnpFactory(AiohttpRequester(timeout=5), non_strict=True)
            device = await factory.async_create_device(location)
        except Exception as exc:  # one odd device must not hide another router
            log.info("UPnP device at %s didn't answer: %s", location, exc)
            continue
        if IgdDevice.is_profile_device(device):
            return Router(IgdDevice(device, None), urlparse(location).hostname or "", device.name)
    raise UpnpError("no_router", "No router answered UPnP on the home network.")


def local_address_towards(host: str) -> str:
    """This PC's address on the network the router is on (no packet is
    sent: a UDP socket only picks the route)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((host, 1900))
        return str(probe.getsockname()[0])


def address_kind(address: str | None) -> str:
    """public, shared (carrier-grade NAT), private (another router in front)
    or none."""
    try:
        ip = ipaddress.ip_address(address or "")
    except ValueError:
        return "none"
    if ip in SHARED_NET:
        return "shared"
    return "public" if ip.is_global else "private"


async def public_address(router: Router) -> str | None:
    return await router.device.async_get_external_ip_address()


async def open_ports(ports: list[tuple[str, int]], description: str) -> Opened:
    """Ask the router to send each (protocol, port) to this PC, then read
    each one back. Raises UpnpError when it can't."""
    router = await find_router()
    local = local_address_towards(router.host)
    me = ipaddress.IPv4Address(local)
    for protocol, port in ports:
        try:
            await router.device.async_add_port_mapping(
                remote_host=ANY_HOST,
                external_port=port,
                protocol=protocol,
                internal_port=port,
                internal_client=me,
                enabled=True,
                description=description,
                lease_duration=timedelta(0),  # until closed; old routers take only 0
            )
            entry = await router.device.async_get_specific_port_mapping_entry(
                remote_host=ANY_HOST, external_port=port, protocol=protocol
            )
        except Exception as exc:
            raise UpnpError(
                "refused", f"The router said no to {protocol} port {port}: {exc}"
            ) from exc
        if entry is None or str(entry.internal_client) != local:
            raise UpnpError(
                "refused", f"The router didn't keep {protocol} port {port} pointing at this PC."
            )
    try:
        address = await public_address(router)
    except Exception as exc:
        log.info("the router didn't say its internet address: %s", exc)
        address = None
    return Opened(address, local, router.name or router.host, list(ports))


async def close_ports(ports: list[tuple[str, int]]) -> None:
    router = await find_router()
    for protocol, port in ports:
        try:
            await router.device.async_delete_port_mapping(
                remote_host=ANY_HOST, external_port=port, protocol=protocol
            )
        except Exception as exc:  # already gone, or the router restarted
            log.info("could not close %s port %s on the router: %s", protocol, port, exc)
