"""Sign in with Microsoft, so Diagnose server can join with the owner's own
account: online-mode Java servers need a Java account, Bedrock servers and
Bedrock players through Geyser an Xbox account. One sign-in covers both.

It is a device code, like signing in on a game console: the desktop app shows
a short code and the owner types it on Microsoft's page. The test player
(bot/join.js, with the prismarine-auth library) talks to Microsoft. What it
gets back, the tokens that let it log in again without a new code, is kept
only on this PC, encrypted for the agent's Windows account (DPAPI,
``microsoft.bin``), and handed to the test player on stdin for each join. It
is never sent to Firebase, Discord or the phones, and no API answer holds it.
Sign out deletes it. Owner only (``microsoft.account``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from . import joinbot
from .security.records import read_secrets, write_secrets

log = logging.getLogger("msc.microsoft")

KEY_FILE = "microsoft.bin"
CODE_WAIT = 30.0  # how long Microsoft may take to hand out a code
SIGN_IN_TIME = 15 * 60.0  # a code is good for 15 minutes


class Microsoft:
    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / KEY_FILE
        # The sign-in being typed in: {"code", "url"} or {"error"}.
        self.pending: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None

    def account(self) -> dict[str, Any] | None:
        """The saved sign-in, to hand to the test player, or None."""
        saved = read_secrets(self.path)
        try:
            cache = json.loads(saved["cache"])
        except (KeyError, ValueError):
            return None
        return cache if isinstance(cache, dict) else None

    def names(self) -> dict[str, str | None]:
        """The Java name and Xbox gamertag the sign-in has (either can be
        missing: an account may own only one of the two games)."""
        saved = read_secrets(self.path)
        return {"java": saved.get("java") or None, "gamertag": saved.get("gamertag") or None}

    def keep(self, cache: dict[str, Any]) -> None:
        """A join renewed the sign-in. Not saved again after Sign out."""
        saved = read_secrets(self.path)
        if saved.get("cache"):
            write_secrets(self.path, {**saved, "cache": json.dumps(cache)})

    def status(self) -> dict[str, Any]:
        names = self.names() if self.account() is not None else {"java": None, "gamertag": None}
        pending = self.pending or {}
        return {
            "signed_in": self.account() is not None,
            "java_name": names["java"],
            "gamertag": names["gamertag"],
            "code": pending.get("code"),
            "url": pending.get("url"),
            "error": pending.get("error"),
        }

    async def sign_in(self) -> dict[str, Any]:
        """Start a sign-in and answer once Microsoft gave the code to type
        (or said no). The test player keeps waiting for the owner in the
        background and saves the sign-in when they're done."""
        if self._task and not self._task.done() and (self.pending or {}).get("code"):
            return self.status()  # the code on screen is still good
        self.cancel()
        self.pending = {}
        got_code = asyncio.Event()
        cache: dict[str, Any] = {}

        def line(found: dict[str, Any]) -> None:
            nonlocal cache
            if isinstance(found.get("cache"), dict):
                cache = found["cache"]
            elif found.get("code"):
                self.pending = {"code": str(found["code"]), "url": str(found.get("url") or "")}
                got_code.set()
            elif found.get("signed_in") is True and cache:
                write_secrets(
                    self.path,
                    {
                        "cache": json.dumps(cache),
                        "java": str(found.get("java") or ""),
                        "gamertag": str(found.get("gamertag") or ""),
                    },
                )
                self.pending = None
                got_code.set()
            elif "signed_in" in found:
                self.pending = {"error": _why(str(found.get("error") or ""))}
                got_code.set()

        async def go() -> None:
            try:
                await joinbot.run({"do": "signin"}, line, SIGN_IN_TIME)
            except TimeoutError:
                self.pending = {"error": "The code ran out before it was typed in. Try again."}
            except joinbot.BotError as exc:
                self.pending = {"error": str(exc)}
            if self.pending == {} or (self.pending or {}).get("code"):
                self.pending = {"error": "The sign-in stopped before it finished. Try again."}
            got_code.set()

        self._task = asyncio.create_task(go())
        try:
            await asyncio.wait_for(got_code.wait(), CODE_WAIT)
        except TimeoutError:
            self.cancel()
            self.pending = {
                "error": "Microsoft didn't answer. Check this PC's internet and try again."
            }
        return self.status()

    def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.pending = None

    def sign_out(self) -> None:
        self.cancel()
        self.path.unlink(missing_ok=True)


def _why(error: str) -> str:
    if "fetch failed" in error:
        return "Couldn't reach Microsoft from this PC. Check its internet and try again."
    return f"Microsoft said no: {error}" if error else "The sign-in didn't finish. Try again."
