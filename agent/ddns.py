"""A name for this PC's internet address: DuckDNS and No-IP.

Both only give the home address a name (myserver.duckdns.org), so friends
type the name instead of numbers that change. They are not tunnels: the
router port must still be open (agent/internet.py), and they can't help
behind carrier-grade NAT (playit.gg can).

  * no program is installed: the agent sends one HTTPS request to each
    site's update address, and nothing else
  * the DuckDNS token and the No-IP sign-in are kept like other secrets
    (``ddns.bin`` in the data folder, encrypted for the agent's Windows
    account), never logged and never in an API answer
  * a name is only saved after its site said yes to it (house rule 1)
  * one name of each kind per PC: every server on this PC has the same
    internet address, only the port differs
  * No-IP deletes a free name that isn't confirmed by hand every 30 days,
    so the agent alerts a week before. The app can't confirm it for you.
  * No-IP asks update programs to stop after an error answer, so after one
    the agent waits until the owner saves the name again (a "911" from
    No-IP only waits 30 minutes)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from . import __version__, downloads, internet
from .events import Event
from .security.records import read_secrets, write_secrets

if TYPE_CHECKING:
    from .core import AgentCore

log = logging.getLogger("msc.ddns")

KEY_FILE = "ddns.bin"
SETTING = "ddns"  # {"duckdns": {...}, "noip": {...}}, no secrets in it
DUCKDNS = "https://www.duckdns.org/update"
NOIP = "https://dynupdate.no-ip.com/nic/update"
DUCKDNS_SITE = "https://www.duckdns.org"
NOIP_SITE = "https://my.noip.com/dynamic-dns"
# No-IP wants every update program to say who it is and how to reach its
# maker: "Company Program/Version contact". The project's own email.
NOIP_CONTACT = "commandb126@gmail.com"
NOIP_AGENT = f"CommandBlock CommandBlock/{__version__} {NOIP_CONTACT}"

CHECK_EVERY = 5 * 60
DUCKDNS_EVERY = 30 * 60  # DuckDNS reads the address from the request itself
NOIP_EVERY = 24 * 3600  # when the router doesn't say the address; No-IP dislikes "nochg"
NOIP_RETRY = 30 * 60  # after "911"
CONFIRM_DAYS = 30
WARN_DAYS = 7
DAY = 24 * 3600

NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
TOKEN_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
HOST_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
# No-IP's answers that mean "stop sending updates", in plain words.
NOIP_STOP = {
    "nohost": "No-IP doesn't have that name on this account.",
    "badauth": "No-IP refused the username or password.",
    "badagent": "No-IP blocked Command Block's updates. Check for a newer version of the app.",
    "!donator": "That name needs a paid No-IP plan.",
    "abuse": "No-IP blocked this name. Sign in on noip.com to see why.",
}


class DdnsError(RuntimeError):
    """Said to the owner as it is."""


class Refused(DdnsError):
    """The site said no to the name or its sign-in: stop until saved again.
    Any other DdnsError (no internet, the site down) is tried again later."""


class Names:
    """The PC's DuckDNS and No-IP names (one of each), kept up to date."""

    def __init__(self, core: AgentCore) -> None:
        self.core = core
        self.path = Path(core.config.data_dir) / KEY_FILE
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ saved state
    def saved(self) -> dict[str, Any]:
        return self.core.db.get_setting(SETTING, {}) or {}

    def _save(self, kind: str, value: dict[str, Any] | None) -> None:
        found = self.saved()
        if value is None:
            found.pop(kind, None)
        else:
            found[kind] = value
        self.core.db.set_setting(SETTING, found)

    def _secrets(self) -> dict[str, str]:
        return read_secrets(self.path)

    def _keep_secrets(self, **changes: str | None) -> None:
        found = {**self._secrets(), **changes}
        write_secrets(self.path, {k: v for k, v in found.items() if v})

    # ------------------------------------------------------------ what the apps see
    def status(self) -> dict[str, Any]:
        """Never holds the token or the password."""
        saved = self.saved()
        out: dict[str, Any] = {
            "duckdns": None,
            "noip": None,
            "duckdns_site": DUCKDNS_SITE,
            "noip_site": NOIP_SITE,
        }
        duck = saved.get("duckdns")
        if duck:
            out["duckdns"] = {
                "name": f"{duck['name']}.duckdns.org",
                "ip": duck.get("ip"),
                "updated_at": duck.get("at"),
                "problem": duck.get("problem"),
            }
        noip = saved.get("noip")
        if noip:
            confirm_by = float(noip.get("confirmed_at") or 0) + CONFIRM_DAYS * DAY
            out["noip"] = {
                "name": noip["host"],
                "ip": noip.get("ip"),
                "updated_at": noip.get("at"),
                "problem": noip.get("problem"),
                "confirm_by": confirm_by,
                "confirm_soon": time.time() >= confirm_by - WARN_DAYS * DAY,
            }
        return out

    def names(self) -> list[dict[str, Any]]:
        """For the Server address card: the names that work right now."""
        found = self.status()
        return [
            {"provider": kind, "name": found[kind]["name"]}
            for kind in ("duckdns", "noip")
            if found[kind] and not found[kind]["problem"]
        ]

    # ------------------------------------------------------------ DuckDNS
    async def save_duckdns(self, name: str, token: str) -> dict[str, Any]:
        name = name.strip().lower().removesuffix(".duckdns.org")
        token = token.strip().lower()
        if not NAME_RE.match(name):
            raise DdnsError("Type the name you picked on duckdns.org, like myserver.")
        if not TOKEN_RE.match(token):
            raise DdnsError("That isn't a DuckDNS token. Copy it from the top of duckdns.org.")
        async with self._lock:
            ip = await _duckdns(name, token)
            self._keep_secrets(duckdns_token=token)
            self._save("duckdns", {"name": name, "ip": ip, "at": time.time(), "problem": None})
        return self.status()

    async def _update_duckdns(self, saved: dict[str, Any]) -> None:
        token = self._secrets().get("duckdns_token")
        if not token:
            return
        try:
            ip = await _duckdns(saved["name"], token)
        except Refused as exc:
            await self._failed("DuckDNS", f"{saved['name']}.duckdns.org", str(exc))
            self._save("duckdns", {**saved, "problem": str(exc), "at": time.time()})
            return
        except DdnsError as exc:  # couldn't reach DuckDNS: try again in 30 minutes
            log.info("DuckDNS update didn't go through: %s", exc)
            self._save("duckdns", {**saved, "at": time.time()})
            return
        self._save("duckdns", {**saved, "ip": ip, "at": time.time(), "problem": None})

    # ------------------------------------------------------------ No-IP
    async def save_noip(self, host: str, username: str, password: str) -> dict[str, Any]:
        host = host.strip().lower().rstrip(".")
        if not HOST_RE.match(host):
            raise DdnsError("Type the whole name from No-IP, like myserver.ddns.net.")
        if not username or ":" in username or not password:
            raise DdnsError("Type the No-IP username (or DDNS key username) and its password.")
        async with self._lock:
            answer, ip = await _noip(host, username, password, router_address(self.core))
            if answer not in ("good", "nochg"):
                raise DdnsError(_noip_words(answer))
            self._keep_secrets(noip_user=username, noip_password=password)
            now = time.time()
            # The 30 days are counted from today: the owner just made or
            # signed in to the name, so it's the best date the app has.
            self._save(
                "noip",
                {"host": host, "ip": ip, "at": now, "problem": None, "confirmed_at": now},
            )
        return self.status()

    def noip_confirmed(self) -> dict[str, Any]:
        """The owner confirmed the name on noip.com: 30 more days."""
        saved = self.saved().get("noip")
        if not saved:
            raise DdnsError("There's no No-IP name on this PC.")
        self._save("noip", {**saved, "confirmed_at": time.time()})
        return self.status()

    async def _update_noip(self, saved: dict[str, Any], address: str | None) -> None:
        secrets = self._secrets()
        user, password = secrets.get("noip_user"), secrets.get("noip_password")
        if not user or not password:
            return
        try:
            answer, ip = await _noip(saved["host"], user, password, address)
        except DdnsError as exc:  # couldn't reach No-IP: try again later
            log.info("No-IP update didn't go through: %s", exc)
            self._save("noip", {**saved, "at": time.time()})
            return
        if answer in ("good", "nochg"):
            self._save("noip", {**saved, "ip": ip, "at": time.time(), "problem": None})
            return
        words = _noip_words(answer)
        if answer == "911":
            self._save("noip", {**saved, "retry_at": time.time() + NOIP_RETRY})
            return
        await self._failed("No-IP", saved["host"], words)
        self._save("noip", {**saved, "problem": words, "at": time.time()})

    # ------------------------------------------------------------ both
    def remove(self, kind: str) -> dict[str, Any]:
        if kind == "duckdns":
            self._keep_secrets(duckdns_token=None)
        else:
            self._keep_secrets(noip_user=None, noip_password=None)
        self._save(kind, None)
        return self.status()

    async def tick(self) -> None:
        """Every few minutes: update a name when its address changed or when
        it's due, and the No-IP reminder."""
        async with self._lock:
            now = time.time()
            address = router_address(self.core)
            saved = self.saved()
            duck = saved.get("duckdns")
            if duck and not duck.get("problem"):
                # Compared with what the router said last time, not DuckDNS's
                # answer, which can differ (another router in front).
                changed = address is not None and address != duck.get("seen")
                if changed or now - float(duck.get("at") or 0) >= DUCKDNS_EVERY:
                    await self._update_duckdns({**duck, "seen": address})
            noip = saved.get("noip")
            if noip and not noip.get("problem") and now >= float(noip.get("retry_at") or 0):
                # No-IP counts the same address sent again against us, so only
                # when it changed, or once a day when the router doesn't say.
                if address is not None:
                    due = address != noip.get("ip")
                else:
                    due = now - float(noip.get("at") or 0) >= NOIP_EVERY
                if due:
                    await self._update_noip(noip, address)
            await self.remind_confirm()

    async def remind_confirm(self) -> None:
        """Once per 30 days, a week before No-IP would delete the free name."""
        saved = self.saved().get("noip")
        if not saved:
            return
        confirmed = float(saved.get("confirmed_at") or 0)
        if time.time() < confirmed + (CONFIRM_DAYS - WARN_DAYS) * DAY:
            return
        if saved.get("warned_for") == confirmed:
            return
        self._save("noip", {**saved, "warned_for": confirmed})
        await self.core.bus.publish(
            Event(
                type="noip_confirm",
                level="warn",
                message=f"Confirm {saved['host']} on noip.com within a week, "
                "or No-IP deletes the name. In Server settings, Server address: "
                "Open noip.com, confirm it there, then I confirmed it.",
                data={"name": saved["host"], "link": NOIP_SITE},
            )
        )

    async def _failed(self, provider: str, name: str, words: str) -> None:
        await self.core.bus.publish(
            Event(
                type="ddns_failed",
                level="error",
                message=f"{provider} stopped updating {name}: {words}",
                data={"provider": provider, "name": name},
            )
        )

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(CHECK_EVERY)
            try:
                await self.tick()
            except Exception:
                log.exception("DuckDNS / No-IP update failed")

    def start(self) -> None:
        if self.saved():
            self._task = asyncio.create_task(self._loop(), name="ddns")

    def ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="ddns")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()


def router_address(core: AgentCore) -> str | None:
    """The internet address a router told us while opening a port, if any.
    Never guessed: without it, the sites read the address the request came from."""
    for ctx in core.servers.values():
        address = internet.public_address(ctx)
        if address:
            return address
    return None


async def _duckdns(name: str, token: str) -> str | None:
    url = f"{DUCKDNS}?{urlencode({'domains': name, 'token': token, 'ip': '', 'verbose': 'true'})}"
    try:
        raw = await downloads.fetch_bytes(url, max_bytes=1024)
    except downloads.DownloadError as exc:
        raise DdnsError(str(exc)) from exc
    lines = raw.decode("utf-8", errors="replace").split()
    if not lines or lines[0] != "OK":
        raise Refused("DuckDNS said no. Check the name is yours and the token is right.")
    return lines[1] if len(lines) > 1 else None


async def _noip(host: str, user: str, password: str, address: str | None) -> tuple[str, str | None]:
    """No-IP's answer word ("good", "nochg", "badauth"...) and the address it set."""
    query = {"hostname": host, **({"myip": address} if address else {})}
    login = base64.b64encode(f"{user}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {login}", "User-Agent": NOIP_AGENT}
    try:
        raw = await downloads.fetch_bytes(f"{NOIP}?{urlencode(query)}", 1024, headers=headers)
    except downloads.DownloadError as exc:
        if getattr(exc, "status", None) == 401:
            return "badauth", None
        raise DdnsError(str(exc)) from exc
    words = raw.decode("utf-8", errors="replace").split()
    if not words:
        return "911", None
    return words[0], (words[1] if len(words) > 1 else None)


def _noip_words(answer: str) -> str:
    if answer == "911":
        return "No-IP has a problem right now. Try again in half an hour."
    return NOIP_STOP.get(answer, f"No-IP answered something unexpected ({answer[:20]}).")
