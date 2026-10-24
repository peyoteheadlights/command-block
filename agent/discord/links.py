"""Linked accounts: which Discord member plays as which Minecraft player.

On a server that reads chat, ``/link <name>`` gives the member a short code
that only works once, for 10 minutes, when typed in the game chat by exactly
that player. The chat line is matched with the anchored chat pattern
(agent/minecraft/chat.py), so nobody can type a line that looks like another
player's. Codes live in memory only.

A Bedrock server's console doesn't show chat, so there ``/link`` sends a link
request that the owner or a moderator approves in the app, picking the
player from the server's recent players.

A Discord member links up to 3 Minecraft accounts (alts); a Minecraft
account belongs to one member. Accounts are kept by their id: the uuid the
server printed (Floodgate's for Bedrock players on Java), or a Bedrock
player's xuid. Linked accounts are in "Export everything" (the database)
and never in "Get help".
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any

MAX_PER_MEMBER = 3
CODE_LIFE = 600.0
REQUEST_LIFE = 7 * 86400.0
# No 0/O, 1/I/L: a code is read off a screen and typed by hand.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6


class LinkError(ValueError):
    """Written for people."""


@dataclass
class Pending:
    server_id: str
    name: str  # the Minecraft name, as typed in /link
    discord_id: str
    discord_name: str
    expires: float
    reply: Any = None  # called with the result words, to edit the Discord reply


class Links:
    def __init__(self, db):
        self.db = db
        self.codes: dict[str, Pending] = {}

    # -- reading -------------------------------------------------------------
    def of_member(self, discord_id: str) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT * FROM discord_links WHERE discord_id = ? ORDER BY linked_at", (discord_id,)
        )

    def of_server(self, server_id: str) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT * FROM discord_links WHERE server_id = ? ORDER BY name COLLATE NOCASE",
            (server_id,),
        )

    def requests(self, server_id: str) -> list[dict[str, Any]]:
        self.db.execute(
            "DELETE FROM discord_link_requests WHERE created_at < ?",
            (time.time() - REQUEST_LIFE,),
        )
        return self.db.query(
            "SELECT * FROM discord_link_requests WHERE server_id = ? ORDER BY created_at",
            (server_id,),
        )

    # -- linking ---------------------------------------------------------------
    def new_code(
        self, server_id: str, name: str, discord_id: str, discord_name: str, reply: Any = None
    ) -> str:
        self._check_room(discord_id)
        now = time.time()
        for code, pending in list(self.codes.items()):
            # One code at a time per member; old ones go.
            if pending.expires < now or pending.discord_id == discord_id:
                del self.codes[code]
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        self.codes[code] = Pending(
            server_id, name, discord_id, discord_name, now + CODE_LIFE, reply
        )
        return code

    def heard(self, server_id: str, player: str, text: str) -> tuple[Pending, str] | None:
        """A player's chat line. None when it isn't a waiting code (it's
        ordinary chat), else the code's request and what happened: "ok",
        "expired" or "other_player" (the code stays for its own player). A
        code line is never ordinary chat, used or not."""
        code = text.strip().upper()
        pending = self.codes.get(code) if len(code) == CODE_LENGTH else None
        if pending is None or pending.server_id != server_id:
            return None
        if pending.expires < time.time():
            del self.codes[code]
            return pending, "expired"
        if pending.name.lower() != player.lower():
            return pending, "other_player"
        del self.codes[code]
        return pending, "ok"

    def hear(self, ctx, player: str, text: str) -> bool:
        """Called for every player chat line on a server. True when it was a
        link code, which then never reaches the chat log (so the chat bridge
        can't post it)."""
        found = self.heard(ctx.server_id, player, text) if self.codes else None
        if found is None:
            return False
        pending, outcome = found
        if outcome == "expired":
            words = "That code ran out. Use /link again for a new one."
        elif outcome == "other_player":
            words = (
                f"Someone other than {pending.name} typed your code, so nothing was linked. "
                f"It still works for {pending.name}."
            )
        else:
            row = ctx.db.query_one(
                "SELECT username, uuid, edition FROM players WHERE server_id = ? AND username = ?",
                (ctx.server_id, player),
            )
            try:
                if not row or not row["uuid"]:
                    raise LinkError(
                        f"The server didn't say {player}'s account id, so nothing was linked. "
                        "Leave, join again and use /link for a new code."
                    )
                self.link(
                    row["uuid"],
                    row["username"],
                    row["edition"],
                    ctx.server_id,
                    pending.discord_id,
                    pending.discord_name,
                    f"{pending.discord_name} (Discord)",
                )
                ctx.db.audit(
                    "discord_link", user=f"{pending.discord_name} (Discord)", target=player
                )
                words = f"Linked: you play as {row['username']} on {ctx.name}."
            except LinkError as exc:
                words = str(exc)
        if pending.reply:
            pending.reply(words)
        return True

    def link(
        self,
        uuid: str,
        name: str,
        edition: str | None,
        server_id: str,
        discord_id: str,
        discord_name: str,
        by: str,
    ) -> dict[str, Any]:
        owner = self.db.query_one("SELECT discord_id FROM discord_links WHERE uuid = ?", (uuid,))
        if owner and owner["discord_id"] != discord_id:
            raise LinkError(f"{name} is already linked to someone else.")
        if not owner:
            self._check_room(discord_id)
        self.db.execute(
            "INSERT INTO discord_links (uuid, name, edition, server_id, discord_id, discord_name, "
            "linked_at, linked_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(uuid) DO UPDATE SET "
            "name = excluded.name, discord_name = excluded.discord_name",
            (uuid, name, edition, server_id, discord_id, discord_name, time.time(), by),
        )
        return self.db.query_one("SELECT * FROM discord_links WHERE uuid = ?", (uuid,)) or {}

    def unlink(self, uuid: str) -> bool:
        return bool(self.db.execute("DELETE FROM discord_links WHERE uuid = ?", (uuid,)).rowcount)

    def _check_room(self, discord_id: str) -> None:
        if len(self.of_member(discord_id)) >= MAX_PER_MEMBER:
            raise LinkError(
                f"You already have {MAX_PER_MEMBER} linked accounts. Remove one with /unlink first."
            )

    # -- Bedrock ---------------------------------------------------------------
    def request(
        self, server_id: str, name: str, discord_id: str, discord_name: str, channel_id: str | None
    ) -> int:
        self._check_room(discord_id)
        self.db.execute(
            "INSERT INTO discord_link_requests (server_id, name, discord_id, discord_name, "
            "channel_id, created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(server_id, discord_id) "
            "DO UPDATE SET name = excluded.name, channel_id = excluded.channel_id, "
            "created_at = excluded.created_at",
            (server_id, name, discord_id, discord_name, channel_id, time.time()),
        )
        row = self.db.query_one(
            "SELECT id FROM discord_link_requests WHERE server_id = ? AND discord_id = ?",
            (server_id, discord_id),
        )
        return int(row["id"]) if row else 0

    def find_request(self, server_id: str, request_id: int) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM discord_link_requests WHERE id = ? AND server_id = ?",
            (request_id, server_id),
        )
        if row is None:
            raise LinkError("That link request isn't there any more.")
        return row

    def drop_request(self, request_id: int) -> None:
        self.db.execute("DELETE FROM discord_link_requests WHERE id = ?", (request_id,))
