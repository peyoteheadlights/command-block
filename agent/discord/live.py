"""What the bot says about a Minecraft server: /status, /players, /ip, the
self-editing status message and the presence line under the bot's name.

Only measured facts (house rule 1): a player count nobody verified says
Unknown, a server that isn't running says so, and an address the agent
didn't read (the internet one) is never guessed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core import ServerContext

STATES = {
    "OFFLINE": "offline",
    "STARTING": "starting",
    "ONLINE": "online",
    "STOPPING": "stopping",
    "CRASHED": "offline after a crash",
    "RESTARTING": "restarting",
    "RESTART_PENDING": "about to restart by itself",
}


def state_words(state: str) -> str:
    return STATES.get(state, "in an unknown state")


def duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


def status(ctx: ServerContext) -> str:
    """/status: one line."""
    s = ctx.summary()
    name = s["name"]
    if s["state"] != "ONLINE":
        return f"{name} is {state_words(s['state'])}."
    players = s["players_online"]
    playing = f"{players}/{s['max_players']} playing" if players is not None else "players: Unknown"
    version = s["minecraft_version"] or "version Unknown"
    uptime = f"up {duration(s['uptime'])}" if s.get("uptime") else "uptime Unknown"
    return f"{name} is online. {playing}, {version}, {uptime}."


def players(ctx: ServerContext) -> str:
    """/players: who's on, or why that isn't known."""
    state = ctx.server.state.value
    if state != "ONLINE":
        return f"{ctx.name} is {state_words(state)}, so nobody is playing."
    if not ctx.players.verified:
        return f"Who's on {ctx.name} isn't known yet. Try again in a minute."
    names = sorted((p["username"] for p in ctx.players.online()), key=str.lower)
    if not names:
        return f"Nobody is playing on {ctx.name} right now."
    return f"Playing on {ctx.name} ({len(names)}/{ctx.config.server.max_players}): " + ", ".join(
        names
    )


def ip(ctx: ServerContext, shared: str | None, info: dict[str, Any]) -> str:
    """/ip: the address the owner typed for Discord, else the addresses the
    agent read from this PC (``info`` is joininfo.join_info), each saying
    who it works for."""
    java = info.get("java")
    side = java or info.get("bedrock") or {}
    port = side.get("port")
    edition = "Java" if java else "Bedrock"
    if shared:
        lines = [f"Join {ctx.name} ({edition}): `{shared}`"]
        if port and ":" not in shared and not side.get("default_port", True):
            lines.append(f"Port: `{port}`")
        return "\n".join(lines)
    lines = [f"Ways to join {ctx.name} ({edition}) the agent can see:"]
    home = [a["address"] for a in side.get("local") or [] if not a.get("virtual")]
    if home:
        lines.append(f"On the owner's home network: `{home[0]}:{port}`")
    tailscale = (side.get("tailscale") or {}).get("address") or side.get("address")
    if tailscale:
        lines.append(f"On the owner's Tailscale: `{tailscale}:{port}`")
    lines.append(
        "From the internet: the owner hasn't added an address to share yet. Ask them for it."
    )
    if java and info.get("bedrock"):
        lines.append(f"Bedrock players can join on port {info['bedrock'].get('port')} (UDP).")
    return "\n".join(lines)


def status_message(ctx: ServerContext, shared: str | None) -> str:
    """The message in the status channel, edited when it changes. No
    running clock in it (Discord shows <t:...:R> as "2 hours ago" itself),
    so it only changes when something real does."""
    state = ctx.server.state.value
    lines = [f"**{ctx.name}** is {state_words(state)}"]
    if state == "ONLINE":
        if ctx.players.verified:
            names = sorted((p["username"] for p in ctx.players.online()), key=str.lower)
            count = f"{len(names)}/{ctx.config.server.max_players}"
            lines.append(f"Players: {count}" + (f" ({', '.join(names)})" if names else ""))
        else:
            lines.append("Players: Unknown")
        version = ctx.server.status().get("minecraft_version")
        lines.append(f"Version: {version or 'Unknown'}")
        if ctx.server.started_at:
            lines.append(f"Online since <t:{int(ctx.server.started_at)}:R>")
    elif state in ("OFFLINE", "CRASHED"):
        lines.append("Ask for it with /request-start.")
    if shared:
        lines.append(f"Join: `{shared}`")
    return "\n".join(lines)[:2000]


def presence(servers: list[ServerContext]) -> str | None:
    """The line under the bot's name. One server: "3 playing on Survival";
    several: "Survival 3 · Creative offline"."""
    if not servers:
        return None
    if len(servers) == 1:
        ctx = servers[0]
        state = ctx.server.state.value
        if state == "ONLINE" and ctx.players.verified:
            return f"{len(ctx.players.online())} playing on {ctx.name}"
        if state == "ONLINE":
            return f"{ctx.name} is online"
        return f"{ctx.name} is {state_words(state)}"
    parts = []
    for ctx in servers:
        state = ctx.server.state.value
        if state == "ONLINE":
            count = len(ctx.players.online()) if ctx.players.verified else "?"
            parts.append(f"{ctx.name} {count}")
        else:
            parts.append(f"{ctx.name} {'offline' if state in ('OFFLINE', 'CRASHED') else '...'}")
    return " · ".join(parts)[:128]
