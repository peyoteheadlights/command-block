"""The Discord servers the bot is in: the channels it uses, the Discord roles
it made, what each Discord role may do on each Minecraft server, and the
checks that say what's wrong.

"Set up this Discord server" only ever creates what's missing. It finds its
own channels and roles by the ids it saved, so one the owner renamed is left
alone, and it never deletes or renames anything.

A Discord member's permissions are built here as an ``Access``, the same
object a moderator's sign-in carries, so ``permissions.check`` decides for
both (see agent/security/permissions.py).
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..security import permissions as P
from ..security.auth import Access, Principal

# Discord's permission bits (https://discord.com/developers/docs/topics/permissions).
KICK_MEMBERS = 1 << 1
BAN_MEMBERS = 1 << 2
ADMINISTRATOR = 1 << 3
MANAGE_CHANNELS = 1 << 4
ADD_REACTIONS = 1 << 6
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
EMBED_LINKS = 1 << 14
READ_MESSAGE_HISTORY = 1 << 16
MANAGE_ROLES = 1 << 28
CREATE_PRIVATE_THREADS = 1 << 36
SEND_MESSAGES_IN_THREADS = 1 << 38
# What the invite link asks for: what this phase's features use, nothing
# more. Manage Channels is for "Set up this Discord server"; the thread ones
# are for /report's private threads.
INVITE_PERMISSIONS = (
    VIEW_CHANNEL
    | SEND_MESSAGES
    | EMBED_LINKS
    | READ_MESSAGE_HISTORY
    | ADD_REACTIONS
    | MANAGE_ROLES
    | MANAGE_CHANNELS
    | CREATE_PRIVATE_THREADS
    | SEND_MESSAGES_IN_THREADS
)
POSTS = VIEW_CHANNEL | SEND_MESSAGES
# A Level role is handed out to anyone who plays enough, so it may not be one
# that gives power in Discord: admin, kick, ban, timeouts, @everyone pings or
# managing anything (bits 4, 5, 13, 27 to 30, 33, 34 are the Manage ones).
POWERS = (
    ADMINISTRATOR
    | KICK_MEMBERS
    | BAN_MEMBERS
    | MANAGE_CHANNELS
    | 1 << 5  # Manage Server
    | 1 << 13  # Manage Messages
    | 1 << 17  # Mention @everyone
    | 1 << 22  # Mute Members
    | 1 << 23  # Deafen Members
    | 1 << 24  # Move Members
    | 1 << 27  # Manage Nicknames
    | MANAGE_ROLES
    | 1 << 29  # Manage Webhooks
    | 1 << 30  # Manage Expressions
    | 1 << 33  # Manage Events
    | 1 << 34  # Manage Threads
    | 1 << 40  # Timeout Members
)
# A chat channel also gets a reaction on a message the game refused.
CHATS = POSTS | ADD_REACTIONS | READ_MESSAGE_HISTORY

# Channels shared by every Minecraft server the Discord server is for, and
# the ones each of them gets. A shared kind can also be tied per server.
SHARED_CHANNELS = ("alerts", "quests", "events")
SERVER_CHANNELS = ("chat", "status")
KINDS = (*SHARED_CHANNELS, *SERVER_CHANNELS)
CATEGORY = "Command Block"
# The Discord roles setup makes: kind -> (name, colour). The mod role gets
# the default permission level; a "level_<n>" role goes to members at Level
# n (agent/levels.py). The owner can change the Level roles; setup then
# leaves them alone.
ROLES = {
    "mod": ("Minecraft mod", 0x3BA55D),
    "level_5": ("Regular", 0x5865F2),
    "level_10": ("Veteran", 0xFAA61A),
    "level_25": ("Legend", 0xED4245),
}


def invite_url(application_id: str) -> str:
    return (
        "https://discord.com/oauth2/authorize?client_id="
        f"{application_id}&scope=bot+applications.commands&permissions={INVITE_PERMISSIONS}"
    )


# -- what's saved -----------------------------------------------------------
def channels(db, guild_id: str) -> list[dict[str, Any]]:
    return db.query(
        "SELECT kind, server_id, channel_id FROM discord_channels WHERE guild_id = ? "
        "ORDER BY kind, server_id",
        (guild_id,),
    )


def tie(db, guild_id: str, kind: str, server_id: str, channel_id: str | None) -> None:
    if channel_id:
        db.execute(
            "INSERT INTO discord_channels (guild_id, kind, server_id, channel_id) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id, kind, server_id) DO UPDATE SET channel_id = excluded.channel_id",
            (guild_id, kind, server_id, channel_id),
        )
    else:
        db.execute(
            "DELETE FROM discord_channels WHERE guild_id = ? AND kind = ? AND server_id = ?",
            (guild_id, kind, server_id),
        )


def servers_of(db, guild_id: str) -> list[str]:
    """The Minecraft servers a Discord server is for: the ones with a
    channel of their own there."""
    rows = db.query(
        "SELECT DISTINCT server_id FROM discord_channels WHERE guild_id = ? AND server_id != ''",
        (guild_id,),
    )
    return sorted(r["server_id"] for r in rows)


def server_of_channel(db, guild_id: str, channel_id: str) -> str | None:
    row = db.query_one(
        "SELECT server_id FROM discord_channels WHERE guild_id = ? AND channel_id = ? "
        "AND server_id != '' LIMIT 1",
        (guild_id, channel_id),
    )
    return row["server_id"] if row else None


def shared_channel(db, guild_id: str, kind: str) -> str | None:
    """The channel of a kind shared by the Discord server's Minecraft servers."""
    row = db.query_one(
        "SELECT channel_id FROM discord_channels WHERE guild_id = ? AND kind = ? AND server_id = ''",
        (guild_id, kind),
    )
    return row["channel_id"] if row else None


def alert_channels(db, server_id: str, guild_ids) -> list[str]:
    """Where a server's alerts go: in each Discord server it's tied to, its
    own alerts channel, else the shared one."""
    rows = db.query(
        "SELECT guild_id, server_id, channel_id FROM discord_channels WHERE kind = 'alerts' "
        "AND server_id IN ('', ?) ORDER BY server_id DESC",
        (server_id,),
    )
    out: dict[str, str] = {}
    for row in rows:  # the server's own row comes first
        guild = row["guild_id"]
        if guild in guild_ids and guild not in out and server_id in servers_of(db, guild):
            out[guild] = row["channel_id"]
    return list(out.values())


ADDRESSES_KEY = "discord_addresses"


def addresses(db) -> dict[str, str]:
    """The address the owner typed for each server, for /ip and the status message."""
    return db.get_setting(ADDRESSES_KEY, {}) or {}


def set_address(db, server_id: str, address: str | None) -> None:
    saved = addresses(db)
    if address:
        saved[server_id] = address
    else:
        saved.pop(server_id, None)
    db.set_setting(ADDRESSES_KEY, saved)


def our_roles(db, guild_id: str) -> dict[str, str]:
    rows = db.query("SELECT kind, role_id FROM discord_roles WHERE guild_id = ?", (guild_id,))
    return {r["kind"]: r["role_id"] for r in rows}


# -- Levels ------------------------------------------------------------------
LEVELS_KEY = "discord_levels"  # guild id -> its Level settings


def level_roles(db, guild_id: str) -> list[tuple[int, str]]:
    """(Level, role id) for each Level role, lowest first."""
    rows = db.query(
        "SELECT kind, role_id FROM discord_roles WHERE guild_id = ? AND kind GLOB 'level_*'",
        (guild_id,),
    )
    return sorted((int(r["kind"].split("_", 1)[1]), r["role_id"]) for r in rows)


def role_for(level: int, roles: list[tuple[int, str]]) -> str | None:
    """The Level role a member at this Level holds: the highest reached."""
    return next((rid for at, rid in reversed(roles) if level >= at), None)


def level_settings(db, guild_id: str) -> dict[str, Any]:
    """announce: post level-ups; leaderboard_day: 0 (Monday) to 6, or None
    for no weekly leaderboard; custom: the owner changed the Level roles."""
    saved = (db.get_setting(LEVELS_KEY, {}) or {}).get(guild_id) or {}
    return {"announce": True, "leaderboard_day": None, "custom": False, **saved}


def set_level_settings(db, guild_id: str, **changes: Any) -> dict[str, Any]:
    everything = db.get_setting(LEVELS_KEY, {}) or {}
    everything[guild_id] = {**level_settings(db, guild_id), **changes}
    db.set_setting(LEVELS_KEY, everything)
    return everything[guild_id]


def level_role_refused(
    guild_id: str, role: dict, ours: dict[str, str], use: str = "a Level role"
) -> str | None:
    """Why a Discord role can't be handed out for playing (a Level role, a
    quest reward), or None when it can."""
    if role["id"] == guild_id or role.get("managed") or role["id"] == ours.get("mod"):
        return f"{role['name']} can't be {use}."
    if int(role.get("permissions") or 0) & POWERS:
        return (
            f"{role['name']} gives power in Discord (like kicking or managing), so it can't be "
            "handed out for playing. Pick a role without those, or make a new one."
        )
    return None


def set_level_roles(db, guild_id: str, roles: list[tuple[int, str]]) -> None:
    """Replace the Level roles (already checked with level_role_refused).
    Roles that stop being Level roles are "retired": the next time
    everyone's roles are set, members lose them."""
    before = {rid for _at, rid in level_roles(db, guild_id)}
    retired = set(level_settings(db, guild_id).get("retired") or []) | before
    db.execute(
        "DELETE FROM discord_roles WHERE guild_id = ? AND kind GLOB 'level_*'",
        (guild_id,),
    )
    for level, role_id in roles:
        db.execute(
            "INSERT INTO discord_roles (guild_id, kind, role_id) VALUES (?, ?, ?)",
            (guild_id, f"level_{level}", role_id),
        )
    keep = {rid for _at, rid in roles}
    set_level_settings(db, guild_id, custom=True, retired=sorted(retired - keep))


def grants(db, guild_id: str) -> dict[str, dict[str, list[str]]]:
    """role id -> server id -> permissions."""
    out: dict[str, dict[str, list[str]]] = {}
    for row in db.query(
        "SELECT role_id, server_id, permissions FROM discord_role_grants WHERE guild_id = ?",
        (guild_id,),
    ):
        out.setdefault(row["role_id"], {})[row["server_id"]] = json.loads(row["permissions"])
    return out


def set_grant(db, guild_id: str, role_id: str, role_name: str, server_id: str, wanted) -> list[str]:
    """Save what a Discord role may do on a server, made whole like a
    moderator's (permissions.grant refuses anything not per-server, so
    nothing owner-only). An empty set removes the grant."""
    granted = sorted(P.grant(wanted))
    if not granted:
        db.execute(
            "DELETE FROM discord_role_grants WHERE guild_id = ? AND role_id = ? AND server_id = ?",
            (guild_id, role_id, server_id),
        )
        return []
    db.execute(
        "INSERT INTO discord_role_grants (guild_id, role_id, server_id, role_name, permissions) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(guild_id, role_id, server_id) DO UPDATE SET "
        "role_name = excluded.role_name, permissions = excluded.permissions",
        (guild_id, role_id, server_id, role_name[:100], json.dumps(granted)),
    )
    return granted


def member_principal(db, guild_id: str, user: dict, role_ids: list[str]) -> Principal:
    """A Discord member as a sign-in. Everyone in a Discord server sees the
    Minecraft servers it's for (the self-service commands); their Discord
    roles' grants add the rest. @everyone's role id is the guild's id."""
    held = {*role_ids, guild_id}
    per_server: dict[str, set[str]] = {sid: {P.SERVER_VIEW} for sid in servers_of(db, guild_id)}
    for role_id, by_server in grants(db, guild_id).items():
        if role_id in held:
            for sid, perms in by_server.items():
                per_server.setdefault(sid, set()).update(perms)
    access = Access(
        grants={sid: P.grant(perms) for sid, perms in per_server.items()},
        all_servers=False,
        new_server=frozenset(),
    )
    name = user.get("global_name") or user.get("username") or "someone"
    return Principal(user=f"{name} (Discord)", kind="discord", role="helper", access=access)


# -- what Discord says ------------------------------------------------------
def base_permissions(guild_id: str, roles: list[dict], role_ids: list[str]) -> int:
    by_id = {r["id"]: int(r.get("permissions") or 0) for r in roles}
    value = by_id.get(guild_id, 0)
    for role_id in role_ids:
        value |= by_id.get(role_id, 0)
    return -1 if value & ADMINISTRATOR else value


def channel_permissions(
    guild_id: str, roles: list[dict], member_id: str, role_ids: list[str], channel: dict
) -> int:
    """What a member may do in a channel, worked out the way Discord does:
    their roles, then the @everyone, role and member overwrites."""
    value = base_permissions(guild_id, roles, role_ids)
    if value == -1:
        return -1
    overwrites = channel.get("permission_overwrites") or []
    for o in overwrites:
        if o["id"] == guild_id:
            value = (value & ~int(o["deny"])) | int(o["allow"])
    allow = deny = 0
    for o in overwrites:
        if o.get("type") == 0 and o["id"] in role_ids:
            allow |= int(o["allow"])
            deny |= int(o["deny"])
    value = (value & ~deny) | allow
    for o in overwrites:
        if o.get("type") == 1 and o["id"] == member_id:
            value = (value & ~int(o["deny"])) | int(o["allow"])
    return value


def has(value: int, wanted: int) -> bool:
    return value == -1 or value & wanted == wanted


def top_position(roles: list[dict], role_ids: list[str]) -> int:
    return max((r.get("position", 0) for r in roles if r["id"] in role_ids), default=0)


async def inspect(rest, db, guild_id: str, bot_id: str, names: dict[str, str]) -> dict[str, Any]:
    """Read the Discord server and check what the bot needs there. Each
    problem names exactly what to fix; ``names`` is server id -> name."""
    roles = await rest.request("GET", f"/guilds/{guild_id}/roles")
    found = await rest.request("GET", f"/guilds/{guild_id}/channels")
    me = await rest.request("GET", f"/guilds/{guild_id}/members/{bot_id}")
    mine = list(me.get("roles") or [])
    by_channel = {c["id"]: c for c in found}
    by_role = {r["id"]: r for r in roles}
    base = base_permissions(guild_id, roles, mine)
    problems: list[dict[str, Any]] = []
    if not has(base, MANAGE_ROLES):
        problems.append({"id": "manage_roles"})
    if not has(base, MANAGE_CHANNELS):
        problems.append({"id": "manage_channels"})
    top = top_position(roles, mine)
    ours = our_roles(db, guild_id)
    for _level, role_id in level_roles(db, guild_id):
        role = by_role.get(role_id)
        if role and role.get("position", 0) >= top:
            problems.append({"id": "role_too_low", "role": role["name"]})
        if role and level_role_refused(guild_id, role, ours):
            problems.append({"id": "level_role_powers", "role": role["name"]})
    tied = channels(db, guild_id)
    for row in tied:
        channel = by_channel.get(row["channel_id"])
        where = names.get(row["server_id"], "") if row["server_id"] else ""
        if channel is None:
            if row["kind"] != "category":
                problems.append({"id": "channel_gone", "kind": row["kind"], "server": where})
            continue
        if row["kind"] == "category":
            continue
        needs = CHATS if row["kind"] == "chat" else POSTS
        if not has(channel_permissions(guild_id, roles, bot_id, mine, channel), needs):
            problems.append(
                {
                    "id": "channel_blocked",
                    "kind": row["kind"],
                    "channel": channel["name"],
                    "server": where,
                }
            )
    return {
        "problems": problems,
        "roles": [
            {
                "id": r["id"],
                "name": r["name"],
                "position": r.get("position", 0),
                "permissions": str(r.get("permissions") or "0"),
            }
            for r in sorted(roles, key=lambda r: -r.get("position", 0))
            # Bots' own roles can't be given to people; @everyone can.
            if not r.get("managed")
        ],
        "channels": [{"id": c["id"], "name": c["name"]} for c in found if c.get("type") in (0, 5)],
        "tied": [
            {
                **row,
                "name": by_channel[row["channel_id"]]["name"]
                if row["channel_id"] in by_channel
                else None,
            }
            for row in tied
            if row["kind"] != "category"
        ],
    }


def slug(name: str, fallback: str) -> str:
    """A Discord channel name: lower case, dashes, letters and digits."""
    out = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return out[:80] or fallback


async def set_up(rest, db, guild_id: str, servers: dict[str, str]) -> dict[str, Any]:
    """Create what's missing for these Minecraft servers (id -> name): the
    category, the shared channels, each server's own channels and the
    Discord roles. Returns what it made."""
    found = {c["id"]: c for c in await rest.request("GET", f"/guilds/{guild_id}/channels")}
    roles = {r["id"] for r in await rest.request("GET", f"/guilds/{guild_id}/roles")}
    saved = {(r["kind"], r["server_id"]): r["channel_id"] for r in channels(db, guild_id)}
    made: list[str] = []

    async def channel(
        kind: str, server_id: str, name: str, parent: str | None, type_: int = 0
    ) -> str:
        existing = saved.get((kind, server_id))
        if existing in found:
            return existing  # type: ignore[return-value]
        body: dict[str, Any] = {"name": name, "type": type_}
        if parent:
            body["parent_id"] = parent
        created = await rest.request("POST", f"/guilds/{guild_id}/channels", body)
        tie(db, guild_id, kind, server_id, created["id"])
        made.append(created["name"])
        return created["id"]

    category = await channel("category", "", CATEGORY, None, type_=4)
    for kind in SHARED_CHANNELS:
        await channel(kind, "", kind, category)
    several = len(servers) > 1
    for sid, name in servers.items():
        for kind in SERVER_CHANNELS:
            await channel(kind, sid, f"{slug(name, sid)}-{kind}" if several else kind, category)

    ours = our_roles(db, guild_id)
    custom = level_settings(db, guild_id)["custom"]
    for kind, (name, colour) in ROLES.items():
        if ours.get(kind) in roles or (custom and kind.startswith("level_")):
            continue
        created = await rest.request(
            "POST",
            f"/guilds/{guild_id}/roles",
            {"name": name, "color": colour, "permissions": "0", "mentionable": False},
        )
        db.execute(
            "INSERT INTO discord_roles (guild_id, kind, role_id) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, kind) DO UPDATE SET role_id = excluded.role_id",
            (guild_id, kind, created["id"]),
        )
        made.append(created["name"])
        if kind == "mod":
            for sid in servers:
                set_grant(db, guild_id, created["id"], name, sid, P.LEVELS[P.DEFAULT_LEVEL])
    return {"made": made}
