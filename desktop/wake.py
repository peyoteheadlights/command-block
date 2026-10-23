"""Sending a wake signal, and the helping PC's listener (Wake-on-LAN).

A wake signal ("magic packet") is six 0xFF bytes and the PC's MAC address
16 times, sent as a broadcast on the home network: it only works from a
device at home. From anywhere else, the device sends its wake key over
Tailscale to the helping PC (another PC at home with the desktop app), and
that PC sends the signal. The agent's half is agent/wake.py.

The helping PC accepts a request only on its Tailscale address, only with a
valid key (it keeps only their hashes), at most once every 30 seconds, and
only ever wakes the one recorded PC.
"""

from __future__ import annotations

import hashlib
import hmac
import socket
import threading
import time
from collections.abc import Callable

PORT = 47391  # the helping PC listens here, on its Tailscale address only
WAKE_PORT = 9  # where wake signals go (the "discard" port, as is usual)
PREFIX = b"CBWAKE1 "
EVERY = 30.0  # at most one wake this often


def magic_packet(mac: str) -> bytes:
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    if len(raw) != 6:
        raise ValueError(f"{mac!r} isn't a MAC address.")
    return b"\xff" * 6 + raw * 16


def send_wake(mac: str, broadcast: str | None) -> None:
    """The wake signal on this network. Does nothing useful away from home,
    and harms nothing either."""
    packet = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for target in {broadcast or "255.255.255.255", "255.255.255.255"}:
            try:
                sock.sendto(packet, (target, WAKE_PORT))
            except OSError:
                pass  # no network here: the helping PC is the other way


def ask_helper(address: str, port: int, key: str) -> None:
    """One request to the helping PC, over Tailscale (encrypted there)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(PREFIX + key.encode(), (address, port))


class Helper:
    """The helping PC's listener. ``send`` is called with the recorded MAC
    and broadcast address when a valid request comes."""

    def __init__(self, send: Callable[[str, str | None], None] = send_wake) -> None:
        self.send = send
        self.keys: list[str] = []  # SHA-256 of each valid key
        self.mac: str | None = None
        self.broadcast: str | None = None
        self.last = 0.0
        self.woken = 0
        self._sock: socket.socket | None = None

    def update(self, found: dict) -> None:
        """The list from the agent (/api/wake/keys)."""
        self.keys = list(found.get("keys") or [])
        self.mac, self.broadcast = found.get("mac"), found.get("broadcast")

    def request(self, data: bytes) -> bool:
        """One request: wakes the recorded PC when it carries a valid key and
        the last wake was long enough ago."""
        if not data.startswith(PREFIX) or len(data) > 200 or not self.mac:
            return False
        digest = hashlib.sha256(data[len(PREFIX) :]).hexdigest()
        if not any(hmac.compare_digest(digest, k) for k in self.keys):
            return False
        now = time.monotonic()
        if self.woken and now - self.last < EVERY:
            return False
        self.last, self.woken = now, self.woken + 1
        self.send(self.mac, self.broadcast)
        return True

    def listen(self, address: str, port: int = PORT) -> None:
        """Starts listening on ``address`` (this PC's Tailscale address)."""
        self.stop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((address, port))
        sock.settimeout(1.0)  # so stop() is noticed within a second
        self._sock = sock

        def serve() -> None:
            while self._sock is sock:
                try:
                    data, _sender = sock.recvfrom(256)
                except TimeoutError:
                    continue
                except OSError:
                    return  # stopped
                try:
                    self.request(data)
                except (OSError, ValueError):
                    pass

        threading.Thread(target=serve, name="help-wake", daemon=True).start()

    def stop(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    @property
    def listening(self) -> bool:
        return self._sock is not None
