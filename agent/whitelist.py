"""Whitelist help for every app: whitelist add-ons on offline-mode servers,
whitelist requests, and the addresses an IP whitelist add-on turned away.

Nothing here knows about Discord: the bot, the API and the phone app all
call it (``core.whitelist``).

**Whitelist add-ons.** On an offline-mode server Minecraft can't check who
an account belongs to, so its own whitelist doesn't work well by name. When
one of the add-ons in WHITELIST_ADDONS is installed there, every whitelist
button (Players page, phone, Discord) sends that add-on's command instead.
Online-mode servers always use Minecraft's own whitelist. The commands and
the lines each add-on prints were read from the add-ons' own jars.

**Whitelist requests.** Someone without the Players permission asks to be
let in (``/whitelist ask``, a Minecraft name and why). The owner or a
moderator approves it in the app or with the button in Discord's alerts
channel; approving sends the normal whitelist command, "Sent" until the
console confirms it.

**New addresses.** With an IP whitelist add-on (IP_ADDONS) a friend whose
home address changed is turned away. When the console says so for a player
this server knows, the address is kept encrypted (AES-GCM, the key in
``.env`` as MCSC_ADDRESS_KEY), shown only masked (1.2.x.x), and the owner and
moderators get "Sam tried to join from a new address" with Allow / Ignore.
Allow sends the add-on's command. Addresses are deleted when the player is
taken off the whitelist (and taken off the add-on's list), when the add-on
is gone, or with Remove in the app. They're in "Export everything" and
never in "Get help".
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from .events import Event
from .minecraft.playeractions import (
    ACTIONS,
    DONE,
    Action,
    PlayerActionError,
    check_name,
)
from .minecraft.properties import FILENAME, PropertiesFile
from .mods.manager import ModError

if TYPE_CHECKING:
    from .core import AgentCore, ServerContext

log = logging.getLogger("msc.whitelist")

KEY_ENV = "MCSC_ADDRESS_KEY"
FORM_LIFE = 7 * 86400.0
MAX_FORMS = 30  # open requests per server
MAX_WHY = 300
REMOVALS_KEY = "whitelist_removals"  # server id -> names to take off once it's online
MAX_REMOVALS = 200
# A colour code some consoles leave in front of a plugin's line.
COLOUR = r"(?:§.)*"


class WhitelistError(ValueError):
    """Written for people."""


@dataclass(frozen=True)
class Addon:
    id: str  # the mod id or plugin name its jar declares
    name: str  # as people know it
    actions: dict[str, Action]


def _vanilla(command: str, kind: str) -> Action:
    """An add-on whose command calls Minecraft's own whitelist code, so the
    console prints Minecraft's own answer."""
    return replace(ACTIONS[kind], command=command)


WHITELIST_ADDONS = (
    Addon(
        "easywhitelist",
        "EasyWhitelist",
        {
            "whitelist_add": _vanilla("easywhitelist add {name}", "whitelist_add"),
            "whitelist_remove": _vanilla("easywhitelist remove {name}", "whitelist_remove"),
        },
    ),
    Addon(
        "zerowhitelist",
        "Zero Whitelist",
        {
            "whitelist_add": _vanilla("zwl add {name}", "whitelist_add"),
            "whitelist_remove": _vanilla("zwl remove {name}", "whitelist_remove"),
        },
    ),
    Addon(
        "textwhitelist",
        "TextWhitelist",
        {
            "whitelist_add": Action(
                "wltxt add player {name}",
                done=(COLOUR + r"{name} has been added to the \w+ list",),
                unchanged=(COLOUR + r"{name} is already on the \w+ list",),
            ),
            "whitelist_remove": Action(
                "wltxt remove player {name}",
                done=(COLOUR + r"{name} has been removed from the \w+ list",),
                unchanged=(COLOUR + r"{name} was not found on the \w+ list",),
            ),
        },
    ),
)
# What the apps suggest for an offline-mode server without one.
SUGGESTED = [a.name for a in WHITELIST_ADDONS]

NAME = r"(?P<name>\.?\w{1,16})"
ADDRESS = r"(?P<ip>[0-9A-Fa-f.:%]+)"


@dataclass(frozen=True)
class IpAddon:
    id: str
    name: str
    # Console lines saying it turned a player away (name and ip groups).
    refused: tuple[re.Pattern, ...]
    allow: Action  # {name} and {ip} filled in
    remove: Action
    # Its line is Minecraft's own "not white-listed": only counted when the
    # name whitelist couldn't have been the reason.
    vanilla_line: bool = False
    # Allow for a player not on its list at all (IPWL's "Player not in whitelist").
    allow_new: Action | None = None


IP_ADDONS = (
    IpAddon(
        "ipwl",
        "IPWL",
        (
            re.compile(
                rf"^\[IPWL SECURITY\] Blocked {NAME} from {ADDRESS}: "
                r"(?P<why>IP mismatch|Player not in whitelist)"
            ),
        ),
        allow=Action("ipwl addip {name} {ip}", done=(r"Added IP {ip} to {name}\.",)),
        allow_new=Action("ipwl add {name} {ip}", done=(r"Added {name} with IP {ip}\.",)),
        remove=Action(
            "ipwl removeip {name} {ip}",
            done=(r"Removed IP {ip} from {name}\.",),
            unchanged=(r"IP not found for {name}",),
        ),
    ),
    IpAddon(
        "ipwhitelist",
        "IP Whitelist",
        (
            re.compile(
                rf"^Disconnecting {NAME} \(/\[?{ADDRESS}\]?:\d+\): {COLOUR}You are not white-listed"
            ),
            re.compile(
                rf"^{NAME} \(/\[?{ADDRESS}\]?:\d+\) lost connection: {COLOUR}You are not white-listed"
            ),
        ),
        allow=Action("ipwhitelist addip {ip}", done=(COLOUR + r"Whitelisted IP \(Manual\): {ip}",)),
        remove=Action("ipwhitelist removeip {ip}", done=(COLOUR + r"Removed IP: {ip}",)),
        vanilla_line=True,
    ),
)
# Cheap first look at every console line, before anything is read from disk.
MIGHT_BE_REFUSED = re.compile(r"Blocked |white-listed")


def mask(address: str) -> str:
    """1.2.x.x, or 2001:db8:x for IPv6: enough to tell two apart, not to find
    anyone."""
    ip = ipaddress.ip_address(address.split("%")[0])
    if ip.version == 4:
        first, second, *_ = str(ip).split(".")
        return f"{first}.{second}.x.x"
    groups = ip.exploded.split(":")
    return f"{groups[0].lstrip('0') or '0'}:{groups[1].lstrip('0') or '0'}:x"


def _with_ip(action: Action, ip: str) -> Action:
    """The add-on's command and answers with one checked address in them."""
    escaped = re.escape(ip)

    def put(patterns: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(p.replace("{ip}", escaped) for p in patterns)

    return replace(
        action,
        command=action.command.replace("{ip}", ip),
        done=put(action.done),
        unchanged=put(action.unchanged),
        failed=put(action.failed),
    )


def online_mode(ctx: ServerContext) -> bool | None:
    """From server.properties; None when it can't be read (not there yet)."""
    path = ctx.config.server_dir / FILENAME
    try:
        value = PropertiesFile.read(path).get("online-mode") if path.is_file() else None
    except (OSError, ValueError):
        return None
    if value is None:
        return None
    return value.strip().lower() != "false"


def installed(ctx: ServerContext) -> set[str]:
    """The ids of the mods or plugins in the server's folder, lower case."""
    if ctx.config.server_type.dialect != "java":
        return set()
    try:
        found = ctx.mods.scan(use_cache=True, max_age=30)
    except (OSError, ModError):
        return set()  # no server folder yet
    return {m.mod_id.lower() for m in found if m.enabled}


def whitelist_addon(ctx: ServerContext) -> Addon | None:
    """The whitelist add-on to use: only on an offline-mode server."""
    if online_mode(ctx) is not False:
        return None
    ids = installed(ctx)
    return next((a for a in WHITELIST_ADDONS if a.id in ids), None)


def ip_addon(ctx: ServerContext) -> IpAddon | None:
    ids = installed(ctx)
    return next((a for a in IP_ADDONS if a.id in ids), None)


def state(ctx: ServerContext) -> dict[str, Any]:
    """What the Players page says about the whitelist."""
    addon = whitelist_addon(ctx)
    ip = ip_addon(ctx)
    return {
        "online_mode": online_mode(ctx),
        "addon": addon.name if addon else None,
        "suggested": SUGGESTED if ctx.config.server_type.dialect == "java" else [],
        "ip_addon": ip.name if ip else None,
    }


def one_line(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


class Whitelist:
    def __init__(self, core: AgentCore):
        self.core = core
        self.db = core.db
        self._key: bytes | None = None
        self._key_lock = asyncio.Lock()
        # Called after a request was answered: (ctx, form, pending action or None).
        self.on_answer: Any = None
        core.bus.subscribe(self._event)

    def stop(self) -> None:
        self.core.bus.unsubscribe(self._event)

    # -- requests ---------------------------------------------------------------
    def forms(self, server_id: str) -> list[dict[str, Any]]:
        self.db.execute(
            "DELETE FROM whitelist_forms WHERE created_at < ?", (time.time() - FORM_LIFE,)
        )
        return self.db.query(
            "SELECT * FROM whitelist_forms WHERE server_id = ? ORDER BY created_at",
            (server_id,),
        )

    def ask(
        self,
        ctx: ServerContext,
        name: str,
        why: str,
        discord_id: str,
        discord_name: str,
        channel_id: str | None,
    ) -> dict[str, Any]:
        """A request to join. One open per member per server (a second one
        replaces it)."""
        try:
            name = check_name(name, ctx.config.server_type.dialect)
        except PlayerActionError as exc:
            raise WhitelistError(str(exc)) from None
        why = one_line(why, MAX_WHY)
        if not why:
            raise WhitelistError("Say a few words about why you'd like to join.")
        mine = self.db.query_one(
            "SELECT id FROM whitelist_forms WHERE server_id = ? AND discord_id = ?",
            (ctx.server_id, discord_id),
        )
        if not mine and len(self.forms(ctx.server_id)) >= MAX_FORMS:
            raise WhitelistError(
                f"{ctx.name} has {MAX_FORMS} requests waiting already. Try again in a few days."
            )
        self.db.execute(
            "INSERT INTO whitelist_forms (server_id, name, why, discord_id, discord_name, "
            "channel_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(server_id, discord_id) DO UPDATE SET name = excluded.name, "
            "why = excluded.why, channel_id = excluded.channel_id, created_at = excluded.created_at",
            (
                ctx.server_id,
                name,
                why,
                discord_id,
                one_line(discord_name, 100),
                channel_id,
                time.time(),
            ),
        )
        form = self.db.query_one(
            "SELECT * FROM whitelist_forms WHERE server_id = ? AND discord_id = ?",
            (ctx.server_id, discord_id),
        )
        assert form is not None
        return form

    async def answer(
        self, ctx: ServerContext, form_id: Any, yes: bool
    ) -> tuple[dict[str, Any], Any]:
        """Approve (sends the whitelist command) or turn down a request. Only
        one of two quick presses gets the request: the row is taken out first."""
        try:
            wanted = int(form_id)
        except (TypeError, ValueError):
            raise WhitelistError("That request isn't there any more.") from None
        form = self.db.query_one(
            "SELECT * FROM whitelist_forms WHERE id = ? AND server_id = ?",
            (wanted, ctx.server_id),
        )
        if form is None:
            raise WhitelistError("That request isn't there any more.")
        if yes and ctx.server.state.value != "ONLINE":
            raise WhitelistError(f"Start {ctx.name} first: the server adds people itself.")
        if not self.db.execute("DELETE FROM whitelist_forms WHERE id = ?", (wanted,)).rowcount:
            raise WhitelistError("Someone else answered that request just now.")
        pending = None
        if yes:
            try:
                pending = await ctx.player_actions.send("whitelist_add", form["name"])
            except PlayerActionError:
                self._put_back(form)
                raise
        if self.on_answer:
            await self.on_answer(ctx, form, pending)
        return form, pending

    def _put_back(self, form: dict[str, Any]) -> None:
        names = ", ".join(form)
        marks = ", ".join("?" for _ in form)
        self.db.execute(
            f"INSERT OR IGNORE INTO whitelist_forms ({names}) VALUES ({marks})",
            tuple(form.values()),
        )

    # -- taking people off --------------------------------------------------------
    async def remove(self, ctx: ServerContext, name: str, by: str) -> str:
        """Take a player off the whitelist now, or once the server is online
        again. Returns "sent" or "later"."""
        if ctx.server.state.value == "ONLINE":
            await ctx.player_actions.send("whitelist_remove", name)
            ctx.db.audit("whitelist_remove", user=by, target=name)
            return "sent"
        waiting = self.db.get_setting(REMOVALS_KEY, {}) or {}
        names = [n for n in waiting.get(ctx.server_id, []) if n.lower() != name.lower()]
        waiting[ctx.server_id] = [*names, name][-MAX_REMOVALS:]
        self.db.set_setting(REMOVALS_KEY, waiting)
        ctx.db.audit("whitelist_remove_later", user=by, target=name)
        return "later"

    async def _removals(self, ctx: ServerContext) -> None:
        waiting = self.db.get_setting(REMOVALS_KEY, {}) or {}
        names = waiting.pop(ctx.server_id, [])
        if not names:
            return
        self.db.set_setting(REMOVALS_KEY, waiting)
        for name in names:
            try:
                await ctx.player_actions.send("whitelist_remove", name)
            except PlayerActionError as exc:
                log.info("couldn't take %s off the whitelist: %s", name, exc)

    async def _event(self, event: Event) -> None:
        ctx = self.core.servers.get(event.server_id or "")
        if ctx is None:
            return
        if event.type == "server_started":
            if ip_addon(ctx) is None:
                self.purge(ctx.server_id)
            await self._removals(ctx)
        elif event.type == "player_action":
            data = event.data or {}
            if data.get("action") == "whitelist_remove" and data.get("username"):
                await self.forget(ctx, str(data["username"]))

    # -- addresses --------------------------------------------------------------
    async def key(self, create: bool = False) -> bytes | None:
        """The addresses' key from .env, made the first time it's needed."""
        if self._key:
            return self._key
        async with self._key_lock:
            saved = os.environ.get(KEY_ENV, "")
            if not saved and create:
                from installer.setup_tool import write_env_value

                from .appinfo import default_env_path

                saved = base64.b64encode(secrets.token_bytes(32)).decode()
                path = self.core.config.env_path or default_env_path()
                await asyncio.to_thread(write_env_value, path, KEY_ENV, saved)
                os.environ[KEY_ENV] = saved
            if saved:
                try:
                    found = base64.b64decode(saved, validate=True)
                except ValueError:
                    found = b""
                if len(found) == 32:
                    self._key = found
            return self._key

    def _digest(self, key: bytes, server_id: str, name: str, ip: str) -> str:
        message = f"{server_id}\n{name.lower()}\n{ip}".encode()
        return hmac.new(key, message, hashlib.sha256).hexdigest()[:32]

    def _seal(self, key: bytes, server_id: str, name: str, ip: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = secrets.token_bytes(12)
        sealed = AESGCM(key).encrypt(nonce, ip.encode(), f"{server_id}\n{name}".encode())
        return base64.b64encode(nonce + sealed).decode()

    def _open(self, key: bytes, row: dict[str, Any]) -> str:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            raw = base64.b64decode(row["address"])
            ip = AESGCM(key).decrypt(
                raw[:12], raw[12:], f"{row['server_id']}\n{row['name']}".encode()
            )
            return str(ipaddress.ip_address(ip.decode()))
        except (ValueError, InvalidTag):
            raise WhitelistError(
                "That address can't be read any more (the key in .env changed). Remove it; "
                "the next time the player is turned away it's asked again."
            ) from None

    def addresses(self, ctx: ServerContext) -> list[dict[str, Any]]:
        """The masked addresses kept for this server (none without an IP
        whitelist add-on: then they're deleted)."""
        if ip_addon(ctx) is None:
            self.purge(ctx.server_id)
            return []
        return self.db.query(
            "SELECT id, name, masked, why, state, seen_at, answered_by, answered_at "
            "FROM ip_addresses WHERE server_id = ? ORDER BY seen_at DESC",
            (ctx.server_id,),
        )

    def purge(self, server_id: str) -> None:
        self.db.execute("DELETE FROM ip_addresses WHERE server_id = ?", (server_id,))

    def _known(self, ctx: ServerContext, name: str) -> bool:
        """Someone this server knows: played here, linked, or whitelisted."""
        if self.db.query_one(
            "SELECT 1 FROM players WHERE server_id = ? AND username = ? COLLATE NOCASE",
            (ctx.server_id, name),
        ) or self.db.query_one(
            "SELECT 1 FROM discord_links WHERE server_id = ? AND name = ? COLLATE NOCASE",
            (ctx.server_id, name),
        ):
            return True
        return name.lower() in self._whitelisted(ctx)

    def _whitelisted(self, ctx: ServerContext) -> set[str]:
        from .minecraft.playeractions import read_list

        found = read_list(ctx.config.server_dir, "whitelist")["players"] or []
        return {p["name"].lower() for p in found}

    async def heard(self, ctx: ServerContext, line: Any) -> dict[str, Any] | None:
        """A console line: when the IP whitelist add-on turned away a player
        this server knows, keep the address and alert. Returns the row."""
        text = (line.message or line.raw).strip()
        if line.source != "stdout" or not MIGHT_BE_REFUSED.search(text):
            return None
        addon = ip_addon(ctx)
        if addon is None:
            return None
        found = next((m for p in addon.refused if (m := p.match(text))), None)
        if found is None:
            return None
        name = found.group("name")
        try:
            ip = str(ipaddress.ip_address(found.group("ip").split("%")[0]))
        except ValueError:
            return None  # "log-ips=false" hides it: nothing to keep
        if addon.vanilla_line and not self._ip_refusal(ctx, name):
            return None
        if not self._known(ctx, name):
            return None  # a stranger: nothing kept
        key = await self.key(create=True)
        if key is None:
            return None
        digest = self._digest(key, ctx.server_id, name, ip)
        why = "new" if found.groupdict().get("why") == "Player not in whitelist" else "changed"
        made = self.db.execute(
            "INSERT OR IGNORE INTO ip_addresses (server_id, name, address, masked, digest, why, "
            "state, seen_at) VALUES (?, ?, ?, ?, ?, ?, 'new', ?)",
            (
                ctx.server_id,
                name,
                self._seal(key, ctx.server_id, name, ip),
                mask(ip),
                digest,
                why,
                time.time(),
            ),
        ).rowcount
        row = self.db.query_one(
            "SELECT id, name, masked, why, state FROM ip_addresses WHERE server_id = ? "
            "AND digest = ?",
            (ctx.server_id, digest),
        )
        if not made or row is None:
            return None  # asked about already (or Ignored): one alert per address
        await ctx.bus.publish(
            Event(
                type="new_address",
                level="warn",
                message=f"{name} tried to join from a new address ({row['masked']})",
                data={"username": name, "address_id": row["id"], "masked": row["masked"]},
            )
        )
        return row

    def _ip_refusal(self, ctx: ServerContext, name: str) -> bool:
        """Minecraft's own "not white-listed" line is the IP add-on's only
        when the name whitelist wouldn't have refused this player."""
        path = ctx.config.server_dir / FILENAME
        try:
            on = PropertiesFile.read(path).get("white-list") if path.is_file() else None
        except (OSError, ValueError):
            on = None
        if (on or "false").strip().lower() != "true":
            return True
        return name.lower() in self._whitelisted(ctx)

    async def allow(self, ctx: ServerContext, address_id: Any, by: str) -> Any:
        """Add the address to the add-on's list. One of two quick presses
        sends it."""
        addon = ip_addon(ctx)
        if addon is None:
            raise WhitelistError(f"{ctx.name} has no IP whitelist add-on any more.")
        row = self._row(ctx, address_id)
        key = await self.key()
        if key is None:
            raise WhitelistError("The addresses' key is missing from .env.")
        ip = self._open(key, row)
        if ctx.server.state.value != "ONLINE":
            raise WhitelistError(f"Start {ctx.name} first: the add-on adds the address itself.")
        if not self.db.execute(
            "UPDATE ip_addresses SET state = 'allowed', answered_by = ?, answered_at = ? "
            "WHERE id = ? AND state != 'allowed'",
            (by, time.time(), row["id"]),
        ).rowcount:
            raise WhitelistError("That address is allowed already.")
        action = addon.allow_new if row["why"] == "new" and addon.allow_new else addon.allow
        try:
            pending = await self._send(ctx, "address_allow", row["name"], action, ip)
        except PlayerActionError:
            self.db.execute(
                "UPDATE ip_addresses SET state = ? WHERE id = ?", (row["state"], row["id"])
            )
            raise
        return pending

    def ignore(self, ctx: ServerContext, address_id: Any, by: str) -> None:
        row = self._row(ctx, address_id)
        self.db.execute(
            "UPDATE ip_addresses SET state = 'ignored', answered_by = ?, answered_at = ? "
            "WHERE id = ?",
            (by, time.time(), row["id"]),
        )

    async def drop(self, ctx: ServerContext, address_id: Any) -> None:
        """Remove: off the add-on's list (when it was allowed) and deleted."""
        row = self._row(ctx, address_id)
        await self._unlist(ctx, [row])
        self.db.execute("DELETE FROM ip_addresses WHERE id = ?", (row["id"],))

    async def forget(self, ctx: ServerContext, name: str) -> None:
        """The player was taken off the whitelist: their addresses go too."""
        rows = self.db.query(
            "SELECT * FROM ip_addresses WHERE server_id = ? AND name = ? COLLATE NOCASE",
            (ctx.server_id, name),
        )
        if not rows:
            return
        await self._unlist(ctx, rows)
        self.db.execute(
            "DELETE FROM ip_addresses WHERE server_id = ? AND name = ? COLLATE NOCASE",
            (ctx.server_id, name),
        )

    async def _unlist(self, ctx: ServerContext, rows: list[dict[str, Any]]) -> None:
        addon = ip_addon(ctx)
        key = await self.key()
        if addon is None or key is None or ctx.server.state.value != "ONLINE":
            return
        for row in rows:
            if row["state"] != "allowed":
                continue
            try:
                await self._send(
                    ctx, "address_remove", row["name"], addon.remove, self._open(key, row)
                )
            except (PlayerActionError, WhitelistError) as exc:
                log.info("couldn't take an address off %s's list: %s", ctx.name, exc)

    async def _send(self, ctx: ServerContext, kind: str, name: str, action: Action, ip: str) -> Any:
        """The add-on's command for one checked address, "Sent" until it answers."""
        ip = str(ipaddress.ip_address(ip))
        filled = _with_ip(action, ip)
        command = filled.command.replace("{name}", check_name(name, "java"))
        pending = await ctx.player_actions.send_built(kind, name, command, filled)
        pending.hide[ip] = mask(ip)  # the apps show 1.2.x.x, never the address
        return pending

    def _row(self, ctx: ServerContext, address_id: Any) -> dict[str, Any]:
        try:
            wanted = int(address_id)
        except (TypeError, ValueError):
            wanted = 0
        row = self.db.query_one(
            "SELECT * FROM ip_addresses WHERE id = ? AND server_id = ?", (wanted, ctx.server_id)
        )
        if row is None:
            raise WhitelistError("That address isn't there any more.")
        return row


def settled_words(ctx: ServerContext, name: str, pending: Any) -> str:
    """What a whitelist add from Discord came to, from the console's answer."""
    word = "allowlist" if ctx.config.server_type.dialect == "bedrock" else "whitelist"
    if pending.state == DONE:
        return f"{name} is on the {word} of {ctx.name} now."
    if pending.state == "unchanged":
        return f"{name} was on the {word} of {ctx.name} already."
    if pending.state == "failed":
        return f"{ctx.name} answered: {pending.message or 'it did nothing.'}"
    return f"Sent to {ctx.name}; the server hasn't confirmed it yet."


async def settled(pending: Any, seconds: float = 20.0) -> Any:
    """Waits (a little) for the console's answer to an action."""
    for _ in range(int(seconds * 10)):
        if pending.state != "sent":
            break
        await asyncio.sleep(0.1)
    return pending
