"""Console friends: the MCXboxBroadcast add-on for Geyser.

Xbox, PlayStation and Switch can't type a server address for a home server,
so they can't join one on their own. MCXboxBroadcast (GPL-3.0,
github.com/MCXboxBroadcast/Broadcaster) is a Geyser extension: it signs in
to a Microsoft account and shows the server in that account's friends'
Friends tab. Console friends add the account as a friend and join from there.

It's unofficial and acts like a game client, which may break Microsoft's
rules, so the apps explain the risks before the owner's click (house rule 2)
and the agent refuses without ``accept_risks``. Only on Java servers with
crossplay on, because it lives in Geyser's extensions folder.

Signing in: on start the extension prints a microsoft.com/link page and a
code to the console. ``heard()`` picks them up so the apps can show them
(owner only: whoever types the code first links their own account). The
sign-in it keeps (cache.json in its folder) is deleted when it's turned
off, and backups leave it out (agent/backups/manager.py).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .crossplay import GEYSER_CONFIG_FOLDERS
from .waterdog import WaterdogError, download

if TYPE_CHECKING:
    from .core import ServerContext
    from .minecraft.console import ConsoleLine

REPO = "MCXboxBroadcast/Broadcaster"
JAR = "MCXboxBroadcastExtension.jar"
# Geyser makes the extension's folder from its id (extension.yml).
FOLDER = "mcxboxbroadcast"
# Geyser puts "[mcxboxbroadcast]" (the extension's id) before every line
# the extension logs; the Paper plugin adds "[Geyser-Spigot]" before that.
# Anchored at the start of the message, so a player's chat can't pass for
# one. Only Microsoft's own sign-in pages are shown.
OURS = re.compile(r"^(?:\[Geyser[^\]]*\]\s*)?\[mcxboxbroadcast\]\s*(?P<rest>.*)$", re.IGNORECASE)
SIGN_IN = re.compile(
    r"To sign in, use a web browser to open the page "
    r"(?P<url>https://(?:www\.)?microsoft\.com/(?:[a-z-]+/)?link|https://login\.live\.com/\S+)"
    r" and enter the code (?P<code>[A-Z0-9-]{4,16}) to authenticate"
)
SIGNED_IN = re.compile(r"Successfully authenticated as (?P<tag>.+?) \(\d+\)")
STARTING = "Starting MCXboxBroadcast Extension"
PROBLEMS = (
    "requiring age verification",
    "Sign in with username and password",
    "Failed to create xbox session",
    "Failed to get/refresh auth token",
)

# server id -> {"sign_in", "gamertag", "problem"} heard while it runs
_heard: dict[str, dict[str, Any]] = {}


class ConsoleFriendsError(RuntimeError):
    """Said to the owner as it is."""


def folder(ctx: ServerContext) -> Path | None:
    """Geyser's extensions folder on this server, or None for kinds without Geyser."""
    parts = GEYSER_CONFIG_FOLDERS.get(ctx.config.server_type.geyser_platform or "")
    return ctx.config.server_dir.joinpath(*parts, "extensions") if parts else None


def status(ctx: ServerContext, owner: bool) -> dict[str, Any]:
    """For the Bedrock players card. Only what's on disk or was heard."""
    where = folder(ctx)
    on = bool(where and (where / JAR).is_file())
    heard = _heard.get(ctx.server_id, {}) if on and ctx.server.running else {}
    return {
        "available": bool(ctx.config.server.crossplay and where),
        "on": on,
        # Whoever types the code links their own account: owner only.
        "sign_in": heard.get("sign_in") if owner else None,
        "gamertag": heard.get("gamertag"),
        "problem": heard.get("problem"),
    }


async def turn_on(ctx: ServerContext, accept_risks: bool) -> None:
    if not accept_risks:
        raise ConsoleFriendsError("Read the risks first, then say yes to them.")
    where = folder(ctx)
    if not (ctx.config.server.crossplay and where):
        raise ConsoleFriendsError('Turn on "Let Bedrock players join" first.')
    if ctx.server.running:
        raise ConsoleFriendsError("Stop the server first: the add-on goes into Geyser's files.")
    try:
        await download(where, REPO, None, JAR, JAR)
    except WaterdogError as exc:
        raise ConsoleFriendsError(str(exc)) from exc


async def turn_off(ctx: ServerContext) -> None:
    """Deletes the add-on and its folder, so the Microsoft sign-in goes too."""
    if ctx.server.running:
        raise ConsoleFriendsError("Stop the server first: the add-on is in Geyser's files.")
    where = folder(ctx)
    if where:
        (where / JAR).unlink(missing_ok=True)
        shutil.rmtree(where / FOLDER, ignore_errors=True)
    _heard.pop(ctx.server_id, None)


def heard(ctx: ServerContext, line: ConsoleLine) -> None:
    """The sign-in code, the account's gamertag and problems, from the console."""
    ours = OURS.match(line.message) if line.source == "stdout" else None
    if not ours:
        return
    text = ours["rest"]
    if text.startswith(STARTING):
        _heard[ctx.server_id] = {}
        return
    found = _heard.setdefault(ctx.server_id, {})
    if match := SIGN_IN.search(text):
        found["sign_in"] = {"url": match["url"], "code": match["code"]}
    elif match := SIGNED_IN.search(text):
        found.update(sign_in=None, problem=None, gamertag=match["tag"].strip())
    elif any(words in text for words in PROBLEMS):
        found["problem"] = text
