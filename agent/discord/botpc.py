"""The Bot PC: with several PCs in Your PCs, one of them runs the Discord bot
for all of them (one bot token has one live connection to Discord).

* The owner's pick is the "bot" record (agent/security/records.py): which
  PC, and the token, encrypted with a key made from the account's signing
  key, which DPAPI keeps. Records only go between the owner's PCs, over
  their pinned connection. Only the Bot PC opens the live connection (the
  gateway); a PC that sees another PC named stops its own.
* Every other PC still posts its own servers' alerts, chat and console
  lines, status messages and events with the same token, through Discord's
  REST API: like each PC having a pen, and only the Bot PC having ears. So
  nothing travels through the Bot PC, and alerts still come when it's off.
* The Bot PC's Discord settings (channels, roles, Discord role grants) are
  copied to the others ("bot_settings"), so moving the bot changes nothing
  in Discord.
* Each PC writes what the bot needs to know about it ("bot_news"): its
  servers, the Levels XP and Linked accounts it holds, and how to wake it.
* A Bot link is a key per PC, made by the Bot PC; only its hash is synced.
  The Bot PC sends it with what Discord asked about a server on that PC,
  and that PC takes only what Discord can do
  (permissions.BOT_LINK_CALLS).

A server on another PC is "survival@<pc id>" in Discord (buttons, choices)
and in this PC's Discord settings; a server on this PC is just "survival".
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
from typing import Any

import httpx

from ..security.auth import AuthError
from ..security.tls import pinned_context
from ..yourpcs import PC_HEADER, PcError

log = logging.getLogger("msc.botpc")

BOT, SETTINGS, LINK, NEWS = "bot", "bot_settings", "bot_link", "bot_news"
LINK_HEADER = "X-Command-Block-Bot-Link"
CALL_SECONDS = 2.0  # Discord waits 3 s for the first answer
# The Discord settings copied to the other PCs: each table's columns.
TABLES = {
    "discord_channels": ("guild_id", "kind", "server_id", "channel_id"),
    "discord_roles": ("guild_id", "kind", "role_id"),
    "discord_role_grants": ("guild_id", "role_id", "server_id", "role_name", "permissions"),
}
ADDRESSES = "discord_addresses"
KEPT = ("discord_guild_options", "discord_levels", ADDRESSES, "discord_leaderboards")


class LinkRefused(Exception):
    """Another PC said no to a Bot link call; the words say why."""


class BotPC:
    def __init__(self, core) -> None:
        self.core = core
        self.records = core.auth.records
        self.records.applied[BOT] = self._applied
        self.records.applied[SETTINGS] = self._applied
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    @property
    def me(self) -> str:
        return self.records.pc

    # -- the owner's pick ------------------------------------------------------
    def choice(self) -> dict[str, Any] | None:
        found = self.records.get(BOT, BOT) if self.core.pcs.joined() else None
        return found["body"] if found else None

    def bot_pc(self) -> str | None:
        return (self.choice() or {}).get("pc")

    def _box(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(hmac.new(self.records.keys["signing"], b"bot token", hashlib.sha256).digest())

    def seal(self, token: str) -> str:
        nonce = secrets.token_bytes(12)
        return base64.b64encode(nonce + self._box().encrypt(nonce, token.encode(), None)).decode()

    def unseal(self, sealed: str) -> str:
        """The token, or "" when there is none or it can't be opened (a new
        account key after the key file was lost: paste the token again)."""
        from cryptography.exceptions import InvalidTag

        try:
            raw = base64.b64decode(sealed, validate=True)
            return self._box().decrypt(raw[:12], raw[12:], None).decode() if raw else ""
        except (InvalidTag, ValueError):
            return ""

    def _running_token(self) -> str:
        bot = self.core.discord
        return bot.token if bot is not None else self.core.config.discord_bot_token

    def set_token(self, token: str) -> None:
        """A token pasted (or removed, "") on this PC: this PC runs the bot."""
        old = self.choice() or {}
        self.records.put(
            BOT,
            BOT,
            {
                "pc": self.me,
                "token": self.seal(token) if token else "",
                "home": old.get("home") or self.me,
            },
        )
        if token:
            self.make_links(fresh=True)

    def run_here(self) -> None:
        """ "Run the bot on this PC instead": same token, same settings."""
        token = self.unseal((self.choice() or {}).get("token") or "")
        if not token:
            raise PcError("There's no bot token to move. Paste one on the Discord page.")
        self.set_token(token)

    def wanted(self) -> tuple[str | None, str]:
        """("full", token) for the live bot, ("rest", token) for one that only
        posts this PC's own things, or (None, "")."""
        if not self.core.pcs.joined():
            token = self.core.config.discord_bot_token
            return ("full", token) if token else (None, "")
        choice = self.choice()
        if choice is None:
            token = self._running_token()
            if not token:
                return None, ""
            self.set_token(token)  # the first bot of a joined account: this one
            choice = self.choice() or {}
        token = self.unseal(choice.get("token") or "")
        if not token:
            return None, ""
        return ("full" if choice.get("pc") == self.me else "rest"), token

    async def update(self) -> None:
        """Start, stop or swap this PC's bot to match the pick. A joined PC
        first hears from the others, so an old Bot PC that was off never
        opens a second live connection."""
        if self.core.pcs.joined():
            await self.core.pcs.pulled.wait()
        async with self._lock:
            mode, token = self.wanted()
            bot = self.core.discord
            if bot is not None and mode and (bot.rest_only, bot.token) == (mode == "rest", token):
                return
            if bot is not None:
                self.core.discord = None
                await bot.stop()
            if mode == "rest":
                self.restore()
            if mode:
                from .bot import Bot

                self.core.discord = Bot(self.core, token, rest_only=mode == "rest")
                self.core.discord.start()

    def update_soon(self) -> None:
        try:
            task = asyncio.get_running_loop().create_task(self.update())
        except RuntimeError:
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _applied(self, record: dict[str, Any], old: dict[str, Any] | None) -> None:
        if record["kind"] == BOT:
            self.update_soon()
        elif self.bot_pc() not in (None, self.me):
            self.restore()

    # -- names of servers --------------------------------------------------------
    def to_discord(self, ref: str) -> str:
        """A server as Discord buttons and choices name it: with its PC, once
        there are several PCs."""
        if "@" in ref or not self.core.pcs.joined():
            return ref
        return f"{ref}@{self.me}"

    def local(self, ref: str) -> str:
        """A server named by Discord, as this PC names it. One with no PC
        (a button from before the PCs joined) is the first Bot PC's."""
        sid, _, pc = str(ref).partition("@")
        pc = pc or (self.choice() or {}).get("home") or self.me
        return sid if pc == self.me else f"{sid}@{pc}"

    def name_of(self, pc: str) -> str:
        found = self.records.get("pc", pc, removed=True)
        return str((found or {}).get("body", {}).get("name") or "another PC")

    # -- what each PC says about itself ---------------------------------------
    def publish(self) -> None:
        """This PC's part of what the bot needs, written only when it changed
        (once a minute at most, from the bot's own minute)."""
        from .. import levels

        if not self.core.pcs.joined():
            return
        body = {
            "servers": [
                {"id": sid, "name": ctx.name, "state": ctx.server.state.value}
                for sid, ctx in sorted(self.core.servers.items())
            ],
            "xp": levels.all_xp(self.core.db),
            "links": [
                {"member": r["discord_id"], "name": r["name"], "server": r["server_id"]}
                for r in self.core.db.query(
                    "SELECT discord_id, name, server_id FROM discord_links ORDER BY linked_at"
                )
            ],
            "wake": self.core.wake.for_bot(),
        }
        old = self.records.get(NEWS, self.me)
        if old is None or old["body"] != body:
            self.records.put(NEWS, self.me, body)

    def linked(self) -> list[str]:
        """The other PCs the bot can reach: in Your PCs, with a Bot link."""
        return [p["key"] for p in self.core.pcs.peers() if self.records.get(LINK, p["key"])]

    def news(self, pc: str) -> dict[str, Any]:
        found = self.records.get(NEWS, pc)
        return found["body"] if found else {}

    def remote(self) -> dict[str, dict[str, Any]]:
        """Servers on the other PCs the bot can reach, "sid@pc" -> what that
        PC said (name, state) and the PC's own name."""
        out = {}
        for pc in self.linked():
            for server in self.news(pc).get("servers") or []:
                out[f"{server['id']}@{pc}"] = {**server, "pc": pc, "pc_name": self.name_of(pc)}
        return out

    def remote_xp(self) -> dict[str, int]:
        total: dict[str, int] = {}
        for pc in self.linked():
            for member, xp in (self.news(pc).get("xp") or {}).items():
                total[member] = total.get(member, 0) + int(xp)
        return total

    def remote_links(self, member: str) -> list[dict[str, Any]]:
        return [
            {**row, "pc": pc, "server": f"{row['server']}@{pc}"}
            for pc in self.linked()
            for row in self.news(pc).get("links") or []
            if row["member"] == member
        ]

    # -- Bot links -----------------------------------------------------------------
    def _key(self, pc: str) -> bytes | None:
        return self.records.keys.get(f"bot_link:{pc}")

    def make_links(self, fresh: bool = False) -> None:
        """A Bot link for each PC (on the Bot PC). ``fresh``: new ones for
        all, as when the Bot PC is picked; else only for a PC that never had
        one (a removed link stays removed)."""
        for peer in self.core.pcs.peers():
            had = self.records.get(LINK, peer["key"], removed=True)
            mine = had is not None and had["body"].get("bot") == self.me
            if fresh or had is None or not (had["removed"] or (mine and self._key(peer["key"]))):
                self.add_link(peer["key"])

    def add_link(self, pc: str) -> None:
        key = secrets.token_bytes(32)
        self.records.set_key(f"bot_link:{pc}", key)
        self.records.put(LINK, pc, {"hash": hashlib.sha256(key).hexdigest(), "bot": self.me})

    def remove_link(self, pc: str) -> None:
        """ "Remove bot link", or the PC left Your PCs."""
        if self.records.get(LINK, pc):
            self.records.remove(LINK, pc)
        if self._key(pc):
            self.records.set_key(f"bot_link:{pc}", None)

    def check_link(self, pc: str, key: str) -> None:
        """On a controlled PC: the caller is the Bot PC, still in Your PCs,
        with this PC's Bot link."""
        link = self.records.get(LINK, self.me) if self.core.pcs.joined() else None
        try:
            given = base64.b64decode(key or "", validate=True)
        except ValueError:
            given = b""
        if (
            link is None
            or self.records.get("pc", pc or "") is None
            or link["body"].get("bot") != pc
            or self.bot_pc() != pc
            or not hmac.compare_digest(
                hashlib.sha256(given).hexdigest(), str(link["body"].get("hash") or "")
            )
        ):
            raise AuthError("That isn't this PC's Bot link.", status=403)

    # -- calling another PC (on the Bot PC) -----------------------------------
    async def call(self, pc: str, what: str, body: dict[str, Any]) -> dict[str, Any] | None:
        """Pass on what Discord asked about that PC's server. None when it
        didn't answer in time: "That PC is off"."""
        peer, key = self.records.get("pc", pc), self._key(pc)
        if peer is None or key is None or not self.records.get(LINK, pc):
            raise LinkRefused(f"{self.name_of(pc)} has no Bot link. The owner can add it back.")
        where = peer["body"]
        try:
            answer = await asyncio.wait_for(self._post(where, what, body, key), CALL_SECONDS)
        except (OSError, httpx.HTTPError, TimeoutError) as exc:
            log.info("couldn't reach %s for the bot: %s", self.name_of(pc), type(exc).__name__)
            return None
        if answer.status_code != 200:
            log.info("%s refused a Bot link call: %s", self.name_of(pc), answer.status_code)
            raise LinkRefused(
                f"{self.name_of(pc)} didn't take it. The owner can check its Bot link."
            )
        return answer.json()

    async def _post(self, where, what: str, body, key: bytes) -> httpx.Response:
        host, port = where["address"], int(where["port"])
        context = await asyncio.to_thread(
            pinned_context, host, port, where["fingerprint"], CALL_SECONDS
        )
        async with httpx.AsyncClient(verify=context, timeout=CALL_SECONDS) as http:
            return await http.post(
                f"https://{host}:{port}/api/sync/bot/{what}",
                json=body,
                headers={PC_HEADER: self.me, LINK_HEADER: base64.b64encode(key).decode()},
            )

    def off_words(self, ref: str, starting: bool) -> str:
        """What Discord hears when the server's PC didn't answer. /start
        first tries to wake it, the way the apps' Help wake does."""
        _sid, _, pc = ref.partition("@")
        name = self.name_of(pc)
        server = (self.remote().get(ref) or {}).get("name") or "that server"
        words = f"That PC is off: {server} runs on {name}, which didn't answer."
        if starting and self.wake(pc):
            words += f" Asked {name} to wake up. Try /start again in a few minutes."
        return words

    def wake(self, pc: str) -> bool:
        from .. import wake

        found = self.news(pc).get("wake")
        if not found:
            return False
        try:
            wake.send_wake(found["mac"], found.get("broadcast"))
            if found.get("helper"):
                helper = found["helper"]
                wake.ask_helper(helper["address"], int(helper["port"]), found["key"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.info("couldn't send the wake signal: %s", exc)
            return False
        return True

    async def ping(self, pc: str) -> None:
        """On a controlled PC, when the apps look: is the Bot PC on? Noted
        in Your PCs' own "reached" state."""
        found = self.records.get("pc", pc)
        if found is None:
            return
        where = found["body"]
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    pinned_context,
                    where["address"],
                    int(where["port"]),
                    where["fingerprint"],
                    CALL_SECONDS,
                ),
                CALL_SECONDS + 0.5,
            )
        except (OSError, TimeoutError) as exc:
            self.core.pcs._failed(pc, type(exc).__name__)
            return
        self.core.pcs._reached(pc)

    # -- the Discord settings, copied ----------------------------------------
    def save_settings(self) -> None:
        """On the Bot PC, once a minute: the settings, when they changed."""
        bot = self.core.discord
        if not self.core.pcs.joined() or bot is None:
            return
        db = self.core.db
        tables = {}
        for table in TABLES:
            rows = db.query(f"SELECT * FROM {table} ORDER BY 1, 2, 3")
            for row in rows:
                if row.get("server_id"):
                    row["server_id"] = self.to_discord(row["server_id"])
            tables[table] = rows
        kept = {key: db.get_setting(key) for key in KEPT}
        kept[ADDRESSES] = {self.to_discord(k): v for k, v in (kept[ADDRESSES] or {}).items()}
        reports = {
            gid: {k: v for k, v in r.items() if k != "checked_at"} for gid, r in bot.reports.items()
        }
        body = {"tables": tables, "settings": kept, "guilds": bot.guilds, "reports": reports}
        old = self.records.get(SETTINGS, BOT)
        if old is None or old["body"] != body:
            self.records.put(SETTINGS, BOT, body)

    def restore(self) -> None:
        """On the other PCs: the Bot PC's settings become this PC's."""
        found = self.records.get(SETTINGS, BOT) if self.core.pcs.joined() else None
        if found is None:
            return
        body, db = found["body"], self.core.db
        for table, columns in TABLES.items():
            db.execute(f"DELETE FROM {table}")
            for row in (body.get("tables") or {}).get(table) or []:
                if row.get("server_id"):
                    row["server_id"] = self.local(row["server_id"])
                db.execute(
                    f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' * len(columns))})",
                    tuple(row.get(c, "") for c in columns),
                )
        kept = body.get("settings") or {}
        kept[ADDRESSES] = {self.local(k): v for k, v in (kept.get(ADDRESSES) or {}).items()}
        for key in KEPT:
            db.set_setting(key, kept.get(key))
        bot = self.core.discord
        if bot is not None and bot.rest_only:
            bot.guilds = dict(body.get("guilds") or {})
            bot.reports = dict(body.get("reports") or {})

    def minute(self, bot) -> None:
        """From the bot's own minute, on every PC with a bot."""
        if not self.core.pcs.joined():
            return
        self.publish()
        if not bot.rest_only:
            self.make_links()
            self.save_settings()

    # -- answering the Bot PC (on a controlled PC) ---------------------------
    def answer(self, what: str, body: dict[str, Any]) -> dict[str, Any]:
        from .. import levels

        bot = self.core.discord
        if bot is None:
            raise PcError("The bot isn't running on this PC.")
        if what == "interaction":
            bot.spawn(
                bot.interaction(dict(body.get("interaction") or {}), server=body.get("server"))
            )
        elif what == "message":
            bot.spawn(bot.from_discord(dict(body.get("message") or {})))
        elif what == "member_left":
            bot.spawn(bot.member_left(dict(body.get("data") or {})))
        elif what == "unlink":
            found = [
                r
                for r in self.core.links.of_member(str(body.get("member") or ""))
                if r["name"].lower() == str(body.get("name") or "").lower()
            ]
            return {"ok": True, "done": bool(found) and self.core.links.unlink(found[0]["uuid"])}
        elif what == "leaderboard":
            ids = [s for s in body.get("servers") or [] if s in self.core.servers]
            until = float(body.get("until") or 0) or None
            return {
                "ok": True,
                "played": levels.weekly_playtime(self.core.db, ids, until),
                "members": sorted(levels.members_on(self.core.db, ids)),
            }
        return {"ok": True}

    # -- what the apps see ------------------------------------------------------
    def status(self) -> dict[str, Any] | None:
        choice = self.choice()
        if not choice or not choice.get("token"):
            return None
        pc = str(choice.get("pc"))
        out: dict[str, Any] = {"id": pc, "name": self.name_of(pc), "this": pc == self.me}
        if pc != self.me:
            out["reach"] = self.core.pcs.reach(pc)
        else:
            out["links"] = [
                {
                    "pc": peer["key"],
                    "name": peer["body"].get("name") or "PC",
                    "linked": bool(self.records.get(LINK, peer["key"])),
                }
                for peer in self.core.pcs.peers()
            ]
        return out

    async def split(self) -> None:
        """This PC left Your PCs: it keeps the bot only if it ran it."""
        choice = self.choice()
        if choice is not None:
            keep = self.unseal(choice.get("token") or "") if choice.get("pc") == self.me else ""
            await self.core.save_discord_token(keep)
        for kind in (BOT, SETTINGS, LINK, NEWS):
            for row in self.records.all(kind, removed=True):
                self.records.drop(kind, row["key"])
        for name in [k for k in self.records.keys if k.startswith("bot_link:")]:
            self.records.set_key(name, None)
        self.update_soon()
