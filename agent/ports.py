"""The port manager: one place that knows which ports the servers use.

Minecraft Java servers listen on a TCP port; Geyser (Bedrock players) and
Bedrock servers listen on a UDP port (a Bedrock server on two: server-port
for IPv4, 19132 by default, and server-portv6, 19133). From Bedrock 1.26.50
(NetherNet) a Bedrock server's server-port is TCP too, and players' game
traffic goes to server-udp-ports, which set_udp_ports() makes the same
number as server-port. This module:

  * reads each server's real game port from its server.properties
  * knows each server's Bedrock (Geyser) UDP port when crossplay is on,
    and the web port of a live map add-on (agent/suggestions.py MAPS),
    which is only for Tailscale: nothing here opens a router port
  * checks whether a port is really free on this machine by trying to bind
    it, as TCP or as UDP, whichever that port is for
  * suggests a port that no configured server uses and nothing else holds
  * refuses to start a server whose port or folder collides with another
    running server, naming that server
"""

from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import AgentCore, ServerContext

# What Minecraft itself uses when server.properties does not set a port.
MINECRAFT_DEFAULT_PORT = 25565
# What Minecraft Bedrock tries first, and Geyser's own default.
BEDROCK_DEFAULT_PORT = 19132
SUGGEST_RANGE = 200


def address(host: str, port: int | None, default: int = MINECRAFT_DEFAULT_PORT) -> str:
    """The address players type: the host, with ":port" when the port isn't
    the default (Minecraft only fills in 25565 by itself). The one place
    that builds a join address, for the desktop app's Server address card
    and Discord's /ip and status message."""
    if not host or not port or port == default or ":" in host:
        return host
    return f"{host}:{port}"


@dataclass(frozen=True)
class GamePort:
    port: int
    protocol: str
    source: str  # where the number came from, for the apps

    def to_dict(self) -> dict[str, Any]:
        return {"port": self.port, "protocol": self.protocol, "source": self.source}


def read_properties_port(server_dir: Path, key: str = "server-port") -> int | None:
    """A port from server.properties, or None if it is not set there."""
    path = Path(server_dir) / "server.properties"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    wanted = key
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == wanted:
            try:
                port = int(value.strip())
            except ValueError:
                return None
            return port if 0 < port < 65536 else None
    return None


# Bedrock Dedicated Server's IPv6 port when server.properties doesn't set one.
BEDROCK_DEFAULT_PORT_V6 = 19133


def game_port(
    server_dir: Path,
    detected: int | None = None,
    protocol: str = "tcp",
    default: int = MINECRAFT_DEFAULT_PORT,
) -> GamePort:
    """The port this server listens on (or will listen on): TCP for Java,
    UDP for Bedrock."""
    if detected:
        return GamePort(detected, protocol, "server console")
    configured = read_properties_port(server_dir)
    if configured is not None:
        return GamePort(configured, protocol, "server.properties")
    return GamePort(default, protocol, "Minecraft's default (server.properties does not set one)")


def bedrock_v6_port(server_dir: Path) -> GamePort:
    configured = read_properties_port(server_dir, "server-portv6")
    if configured is not None:
        return GamePort(configured, "udp", "server.properties")
    return GamePort(
        BEDROCK_DEFAULT_PORT_V6, "udp", "Bedrock's default (server.properties does not set one)"
    )


def udp_ports(server_dir: Path) -> str | None:
    """server-udp-ports when it is one port or one range ("19132",
    "19140-19155"), for the router step. None when it isn't set, or is a
    public:local mapping or has an address, which the owner set up themselves."""
    from .minecraft.properties import PropertiesFile

    path = Path(server_dir) / "server.properties"
    try:
        value = (PropertiesFile.read(path).get("server-udp-ports") or "").strip()
    except (OSError, ValueError):
        return None
    return value if re.fullmatch(r"\d{1,5}(-\d{1,5})?", value) else None


def set_udp_ports(server_dir: Path) -> int | None:
    """Bedrock 1.26.50+ (NetherNet) gives each player a random UDP port
    unless server-udp-ports is set, and random ports can't be forwarded or
    let through a firewall. When it isn't set, set it to the game port's
    number, which Mojang lets all players share. Returns the port written,
    or None when nothing was changed (already set, or no server.properties)."""
    from .minecraft.properties import PropertiesFile

    path = Path(server_dir) / "server.properties"
    if not path.is_file():
        return None
    parsed = PropertiesFile.read(path)
    if (parsed.get("server-udp-ports") or "").strip():
        return None  # the owner's (or ours from before): never changed here
    port = read_properties_port(server_dir) or BEDROCK_DEFAULT_PORT
    parsed.set("server-udp-ports", str(port))
    parsed.write(path)
    return port


def is_free(port: int, protocol: str = "tcp", host: str = "0.0.0.0") -> bool:
    """True when nothing on this machine holds the port. Checked by binding
    it, which is the only reliable test; the socket is closed at once."""
    kind = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    sock = socket.socket(socket.AF_INET, kind)
    try:
        if os.name == "nt":
            # Without this Windows lets a second socket share the port.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)  # type: ignore[attr-defined]
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _same_or_nested(a: Path, b: Path) -> bool:
    try:
        a, b = a.resolve(), b.resolve()
    except OSError:
        return False
    return a == b or a in b.parents or b in a.parents


class PortManager:
    def __init__(self, core: AgentCore):
        self.core = core

    def port_of(self, ctx: ServerContext) -> GamePort:
        server_type = ctx.config.server_type
        return game_port(
            ctx.config.server_dir,
            ctx.server.detected_port,
            server_type.game_protocol,
            server_type.default_port,
        )

    def claimed(self, ctx: ServerContext) -> list[GamePort]:
        """Every port this server uses: its game port, a Bedrock server's
        IPv6 port, Geyser's port when crossplay is on, and a live map's."""
        from .suggestions import map_ports

        found = [self.port_of(ctx)]
        if ctx.config.server_type.edition == "bedrock":
            found.append(bedrock_v6_port(ctx.config.server_dir))
            # NetherNet's join page is TCP on the same number.
            found.append(GamePort(found[0].port, "tcp", "NetherNet's join page"))
        geyser = self.bedrock_port_of(ctx)
        if geyser:
            found.append(geyser)
        if ctx.config.server_type.has_content:
            found += [GamePort(m["port"], "tcp", m["source"]) for m in map_ports(ctx) if m["port"]]
        return found

    def bedrock_port_of(self, ctx: ServerContext) -> GamePort | None:
        """The UDP port Bedrock players reach this server on, or None when
        crossplay is off."""
        if not ctx.config.server.crossplay:
            return None
        port = int(ctx.config.server.bedrock_port or 0)
        if not port:
            return None
        return GamePort(port, "udp", "Geyser's settings")

    def used(self, exclude: str | None = None) -> dict[tuple[str, int], str]:
        """(protocol, port) -> server id, for every configured server. Holds
        each server's Java (TCP) port and, with crossplay on, its Bedrock
        (UDP) port."""
        taken: dict[tuple[str, int], str] = {}
        for server_id, ctx in self.core.servers.items():
            if server_id == exclude:
                continue
            for port in self.claimed(ctx):
                taken.setdefault((port.protocol, port.port), server_id)
        return taken

    def suggest_bedrock_server(self) -> tuple[int, int] | None:
        """Two free UDP ports next to each other for a new Bedrock server
        (IPv4 and IPv6), clear of every server's ports and Geyser's."""
        taken = self.used()
        start = BEDROCK_DEFAULT_PORT
        for port in range(start, min(start + SUGGEST_RANGE, 65535), 2):
            pair = (port, port + 1)
            if any(("udp", p) in taken for p in pair) or ("tcp", port) in taken:
                continue
            if all(is_free(p, "udp") for p in pair) and is_free(port, "tcp"):
                return pair
        return None

    def suggest_bedrock(self) -> int | None:
        """A free UDP port for Geyser, checked as UDP."""
        return self.suggest("udp", BEDROCK_DEFAULT_PORT)

    def bedrock_owner(self, port: int, exclude: str | None = None) -> str | None:
        """The name of the server already using this UDP port, or None."""
        owner = self.used(exclude=exclude).get(("udp", int(port)))
        return self.core.servers[owner].name if owner else None

    def suggest(self, protocol: str = "tcp", start: int = MINECRAFT_DEFAULT_PORT) -> int | None:
        """The first port from ``start`` that no server uses and is free here."""
        taken = self.used()
        for port in range(start, min(start + SUGGEST_RANGE, 65536)):
            if (protocol, port) in taken:
                continue
            if is_free(port, protocol):
                return port
        return None

    def start_conflicts(self, ctx: ServerContext) -> list[str]:
        """Reasons this server must not start now because of another one."""
        problems = []
        mine = self.port_of(ctx)
        my_ports = {(p.protocol, p.port) for p in self.claimed(ctx)}
        for other in self.core.servers.values():
            if other is ctx or not other.server.running:
                continue
            theirs = {(p.protocol, p.port) for p in self.claimed(other)}
            said: set[int] = set()
            for protocol, port in sorted(my_ports & theirs):
                if port == mine.port:
                    if port in said:
                        continue  # a Bedrock game port is both TCP and UDP
                    said.add(port)
                    problems.append(
                        f"Port {port} is already used by the running server "
                        f"'{other.name}'. Change server-port in this server's "
                        "server.properties, or stop the other server first."
                    )
                elif protocol == "tcp":
                    problems.append(
                        f"This server's live map would use port {port}, which the running "
                        f"server '{other.name}' already uses. Change the port in the map's "
                        "settings file, or stop the other server first."
                    )
                else:
                    problems.append(
                        f"Bedrock players would use port {port}, which the running "
                        f"server '{other.name}' already uses. Change one of them in "
                        "Server settings, or stop the other server first."
                    )
            if _same_or_nested(ctx.config.server_dir, other.config.server_dir):
                problems.append(
                    f"The folder {ctx.config.server_dir} is already used by the running "
                    f"server '{other.name}'. Two servers cannot run from the same folder."
                )
        return problems

    def start_warnings(self, ctx: ServerContext) -> list[str]:
        warnings = []
        mine = self.port_of(ctx)
        if not is_free(mine.port, mine.protocol):
            warnings.append(
                f"Port {mine.port} looks busy on this PC (another program may be using it), "
                "so Minecraft may fail to start."
            )
        for extra in self.claimed(ctx)[1:]:
            if extra.protocol == "tcp" and extra.port != mine.port:
                continue  # a live map's port, held by the server itself
            if not is_free(extra.port, extra.protocol):
                warnings.append(
                    f"Port {extra.port} ({extra.protocol.upper()}), which Bedrock players use, "
                    "looks busy on this PC, so they may not be able to join."
                )
        return warnings
