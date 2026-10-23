"""Wake-on-LAN, the agent's half (Technical mode only).

While the PC is awake the agent records what a wake signal needs: the MAC
address of the network adapter that carries its home address, and that
network's broadcast address. Devices allowed to wake the PC (the owner, and
moderators with "Start the agent") keep a copy, because a sleeping PC can't
answer.

A wake signal can't cross Tailscale, so waking from outside the home goes
through a helping PC: another PC at home with the desktop app signed in to
this one, left running. Each allowed device gets its own wake key; only its
hash is kept here, on its sign-in, so signing the device out or taking the
permission away ends it. The helping PC fetches the valid hashes and accepts
a request only with one of them (desktop/wake.py).

The agent never changes a Windows setting and runs no program for this: the
adapter is read from Windows' own adapter list.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import secrets
import socket
import sys
import time
from typing import Any

import psutil

log = logging.getLogger("msc.wake")

STATE_KEY = "wake"
CABLE, WIFI = 6, 71  # Windows' adapter kinds (IfType)
TAILSCALE_RANGE = ipaddress.ip_network("100.64.0.0/10")
WATCH_EVERY = 10
# The clock jumping this much more than a watch step means the PC slept.
SLEPT_FOR = 60
TRY_FOR = 24 * 3600  # a "Try it" nobody finished stops waiting after this


class WakeRefused(ValueError):
    """Said to the owner as it is."""


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def home_address() -> str | None:
    """The address this PC uses on its home network: the one its default
    route leaves from. Connecting a UDP socket sends nothing."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # a documentation address; no packet is sent
            address = probe.getsockname()[0]
    except OSError:
        return None
    ip = ipaddress.ip_address(address)
    return None if ip.is_loopback or ip in TAILSCALE_RANGE else address


def adapter_kinds() -> dict[str, int]:
    """Each adapter's MAC and kind (IfType) from Windows' adapter list
    (GetAdaptersAddresses, no admin needed). Empty off Windows."""
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes

    class Adapter(ctypes.Structure):
        pass

    # The start of IP_ADAPTER_ADDRESSES, up to IfType: all this needs.
    Adapter._fields_ = [
        ("Length", wintypes.ULONG),
        ("IfIndex", wintypes.DWORD),
        ("Next", ctypes.POINTER(Adapter)),
        ("AdapterName", ctypes.c_char_p),
        ("FirstUnicastAddress", ctypes.c_void_p),
        ("FirstAnycastAddress", ctypes.c_void_p),
        ("FirstMulticastAddress", ctypes.c_void_p),
        ("FirstDnsServerAddress", ctypes.c_void_p),
        ("DnsSuffix", ctypes.c_wchar_p),
        ("Description", ctypes.c_wchar_p),
        ("FriendlyName", ctypes.c_wchar_p),
        ("PhysicalAddress", ctypes.c_ubyte * 8),
        ("PhysicalAddressLength", wintypes.ULONG),
        ("Flags", wintypes.ULONG),
        ("Mtu", wintypes.ULONG),
        ("IfType", wintypes.DWORD),
    ]
    size = wintypes.ULONG(16 * 1024)
    code = 111
    for _ in range(3):  # 111: the buffer was too small; size now says how big
        buffer = ctypes.create_string_buffer(size.value)
        # AF_UNSPEC, skipping the address lists (flags 1, 2, 4 and 8).
        code = ctypes.windll.iphlpapi.GetAdaptersAddresses(
            0, 0x0F, None, buffer, ctypes.byref(size)
        )
        if code != 111:
            break
    if code != 0:
        return {}
    found: dict[str, int] = {}
    item = ctypes.cast(buffer, ctypes.POINTER(Adapter))
    while item:
        adapter = item.contents
        raw = adapter.PhysicalAddress[: adapter.PhysicalAddressLength]
        if raw:
            found[":".join(f"{b:02x}" for b in raw)] = adapter.IfType
        item = adapter.Next
    return found


def read_adapter() -> dict[str, Any] | None:
    """The adapter with the home address: its name, MAC, the network's
    broadcast address, and whether it's a cable (None: Windows didn't say).
    None when there is no home address."""
    address = home_address()
    if address is None:
        return None
    try:
        adapters = psutil.net_if_addrs()
    except Exception:  # pragma: no cover - psutil reads the OS
        return None
    for name, entries in adapters.items():
        ipv4 = next(
            (e for e in entries if e.family == socket.AF_INET and e.address == address), None
        )
        if ipv4 is None:
            continue
        mac = next((e.address for e in entries if e.family == psutil.AF_LINK), "")
        mac = mac.lower().replace("-", ":")
        if not mac or set(mac) <= {"0", ":"}:
            return None
        network = ipaddress.ip_interface(f"{address}/{ipv4.netmask or '255.255.255.0'}").network
        kind = adapter_kinds().get(mac)
        return {
            "adapter": name,
            "mac": mac,
            "address": address,
            "broadcast": str(network.broadcast_address),
            "wired": True if kind == CABLE else False if kind == WIFI else None,
        }
    return None


class Wake:
    def __init__(self, core) -> None:
        self.core = core
        self._task: asyncio.Task | None = None

    def saved(self) -> dict[str, Any]:
        return self.core.db.get_setting(STATE_KEY, {}) or {}

    def _save(self, **changes: Any) -> dict[str, Any]:
        state = {**self.saved(), **changes}
        self.core.db.set_setting(STATE_KEY, state)
        return state

    def record(self) -> dict[str, Any] | None:
        """Read the adapter, and keep its MAC and broadcast address while
        Wake-on-LAN is on, so a change of network card or router is picked up."""
        found = read_adapter()
        if found and self.saved().get("on") and found.get("wired") is not False:
            self._save(mac=found["mac"], broadcast=found["broadcast"], adapter=found["adapter"])
        return found

    # -------------------------------------------------------- the setup (owner)
    def setup(self) -> dict[str, Any]:
        now = self.record()
        saved = self.saved()
        helper = self.helper()
        return {
            "on": bool(saved.get("on")),
            "now": now,
            "mac": saved.get("mac"),
            "trying_since": saved.get("trying_since"),
            "tested_at": saved.get("tested_at"),
            "helper": {"label": helper["label"], "address": helper["address"]} if helper else None,
            "router_url": saved.get("router_url"),
        }

    def turn_on(self) -> dict[str, Any]:
        found = read_adapter()
        if found is None:
            raise WakeRefused(
                "This PC isn't connected to a home network, so Wake-on-LAN can't work."
            )
        if found["wired"] is False:
            raise WakeRefused(
                "This PC is on Wi-Fi. Wake-on-LAN only works over a network cable: plug one "
                "in, then try again."
            )
        self._save(on=True)
        self.record()
        return self.setup()

    def turn_off(self) -> dict[str, Any]:
        self.core.db.set_setting(STATE_KEY, {})
        self.core.db.execute("UPDATE sessions SET wake_key_hash = NULL")
        return self.setup()

    def set_router(self, url: str | None) -> dict[str, Any]:
        if url and not (url.startswith("https://") and len(url) <= 300 and " " not in url):
            # The apps only open https links (a link could otherwise run a program).
            raise WakeRefused("The router's wake page must be an https:// link.")
        self._save(router_url=url or None)
        return self.setup()

    def try_it(self) -> dict[str, Any]:
        if not self.saved().get("on"):
            raise WakeRefused("Set up Wake-on-LAN first.")
        self._save(trying_since=time.time())
        self.start()
        return self.setup()

    def noticed(self, before: float, after: float) -> bool:
        """One watch step: the clock moved much further than the step, so the
        PC slept and woke. Marks "Tested, worked" once."""
        if after - before < WATCH_EVERY + SLEPT_FOR:
            return False
        self._save(trying_since=None, tested_at=after)
        log.info("Woke from sleep after Try it: Wake-on-LAN marked as tested.")
        return True

    async def _watch(self) -> None:
        last = time.time()
        while True:
            started = self.saved().get("trying_since")
            if not started or time.time() - started > TRY_FOR:
                self._save(trying_since=None)
                return
            await asyncio.sleep(WATCH_EVERY)
            now = time.time()
            if self.noticed(last, now):
                return
            last = now

    def start(self) -> None:
        """Waits for the wake only while a "Try it" is open (house rule 7)."""
        self.record()
        if self.saved().get("trying_since") and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._watch(), name="wake-watch")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    # -------------------------------------------------------- the helping PC
    def helper(self) -> dict[str, Any] | None:
        """The helping PC, while its sign-in lasts."""
        from .security.auth import live_session

        helper = self.saved().get("helper")
        if not helper:
            return None
        alive = self.core.db.query_one(
            f"SELECT 1 FROM sessions WHERE token_hash = ? AND {live_session()}",
            (helper["session"], time.time()),
        )
        return helper if alive else None

    def set_helper(self, session: str | None, address: str, port: int) -> dict[str, Any]:
        if not session:
            raise WakeRefused("Sign in on the helping PC to make it help.")
        try:
            if ipaddress.ip_address(address) not in TAILSCALE_RANGE:
                raise ValueError
        except ValueError as exc:
            raise WakeRefused("The helping PC needs Tailscale connected.") from exc
        row = self.core.db.query_one("SELECT label FROM sessions WHERE token_hash = ?", (session,))
        label = (row or {}).get("label") or "Another PC"
        self._save(helper={"session": session, "address": address, "port": port, "label": label})
        return self.setup()

    def session_moved(self, old: str | None, new: str) -> None:
        """The helping PC's sign-in got a new key (/auth/rotate): it still helps."""
        helper = self.saved().get("helper")
        if helper and old and helper["session"] == old:
            self._save(helper={**helper, "session": new})

    def remove_helper(self) -> dict[str, Any]:
        self._save(helper=None)
        return self.setup()

    def may_wake(self, user: str) -> bool:
        from .security.permissions import AGENT_START

        role, access = self.core.auth._role_of(user)
        return role == "owner" or (access is not None and AGENT_START in access.pc)

    def key_hashes(self) -> list[str]:
        """The wake keys the helping PC accepts: those of current sign-ins
        that may still wake the PC."""
        from .security.auth import live_session

        rows = self.core.db.query(
            "SELECT user, wake_key_hash FROM sessions "
            f"WHERE wake_key_hash IS NOT NULL AND {live_session()}",
            (time.time(),),
        )
        return sorted({r["wake_key_hash"] for r in rows if self.may_wake(r["user"])})

    # -------------------------------------------------------- a device that may wake it
    def for_device(self, session: str | None) -> dict[str, Any]:
        """What this device keeps to wake the PC later. No key: it asks for
        its own with new_key()."""
        saved = self.saved()
        helper = self.helper()
        has_key = False
        if session:
            row = self.core.db.query_one(
                "SELECT wake_key_hash FROM sessions WHERE token_hash = ?", (session,)
            )
            has_key = bool(row and row["wake_key_hash"])
        return {
            "on": bool(saved.get("on") and saved.get("mac")),
            "pc": socket.gethostname(),
            "mac": saved.get("mac"),
            "broadcast": saved.get("broadcast"),
            "helper": {"address": helper["address"], "port": helper["port"]} if helper else None,
            "router_url": saved.get("router_url"),
            "has_key": has_key,
        }

    def new_key(self, session: str | None) -> str:
        if not session:
            raise WakeRefused("Sign in on the device to give it a wake key.")
        key = secrets.token_urlsafe(24)
        self.core.db.execute(
            "UPDATE sessions SET wake_key_hash = ? WHERE token_hash = ?", (key_hash(key), session)
        )
        return key
