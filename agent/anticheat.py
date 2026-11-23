"""The anti-cheat picker: free, paid or your own, one at a time.

Three kinds, shown on the Mods page and pointed to from the Protection card:

- **Free**: on Modrinth, installed with the normal add-on install (its
  version check, one backup, one Undo). Grim on Paper, Purpur, Leaf and
  Fabric; Intave on Paper, Purpur and Leaf.
- **Paid**: sold on the seller's own site, so the app never buys anything.
  A card says what it is and opens the seller's page; the jar comes in
  with "Add from a file" like any other jar.
- **Custom**: your own anti-cheat jar, through "Add own .jar" (needs that
  permission, checked by the upload itself).

Rules: one anti-cheat at a time (two installed is a problem line here, and
the Mods page's "same job" check shows it too, data/catalog.json); none is
offered on a type it doesn't run on; nothing ever says "protected", only
what is installed.

Prices: Vulcan's is read from Spiget (api.spiget.org, a public copy of
SpigotMC's resource list) at most once a day, when the picker is opened,
and shown with the day it was read. A product with no readable price (Polar
sells on its own site) shows the fixed price from the release check, or
none: the card then sends people to the seller's page instead of guessing.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from . import downloads
from .security import permissions as P

if TYPE_CHECKING:
    from .core import ServerContext

log = logging.getLogger("msc.anticheat")

PLUGIN_TYPES = ("paper", "purpur", "leaf")
FREE: tuple[dict[str, Any], ...] = (
    {
        "id": "grim",
        "name": "Grim",
        "project": "grimac",  # Modrinth, GPL-3.0
        "jar_ids": ("grimac",),
        "types": (*PLUGIN_TYPES, "fabric"),
    },
    {
        "id": "intave",
        "name": "Intave",
        "project": "intave",  # Modrinth, PolyForm Perimeter (free to use)
        "jar_ids": ("intave",),
        "types": PLUGIN_TYPES,
    },
)
PAID: tuple[dict[str, Any], ...] = (
    {
        "id": "vulcan",
        "name": "Vulcan",
        "jar_ids": ("vulcan",),
        "types": PLUGIN_TYPES,
        "link": "https://www.spigotmc.org/resources/83626/",
        "setup": None,
        "price": None,  # the release check's price, used when Spiget can't be read
        "spiget": 83626,  # its SpigotMC resource id
    },
    {
        "id": "polar",
        "name": "Polar",
        "jar_ids": ("polarloader",),
        "types": PLUGIN_TYPES,
        "link": "https://polar.top",
        "setup": "https://docs.polar.top/getting-started.html",
        "price": None,
    },
)
# When the fixed prices above were checked ("November 2026"); None while none is.
PRICES_CHECKED: str | None = None
SPIGET = "https://api.spiget.org/v2/resources/{}"
PRICES_SETTING = "anticheat_prices"  # id -> {"price": "19.99 USD", "read_at": time}
READ_EVERY = 24 * 3600
_CURRENCY = re.compile(r"^[a-z]{3}$")
# Other anti-cheats people use, only to see that one is in (never offered).
OTHERS = {"spartan": "Spartan", "matrix": "Matrix", "themis": "Themis", "karhu": "Karhu"}


def supported(type_id: str) -> bool:
    return any(type_id in entry["types"] for entry in (*FREE, *PAID))


def installed(addons: set[str]) -> list[str]:
    """The anti-cheats among the turned-on jars (by their own ids, lower case)."""
    found = [e["name"] for e in (*FREE, *PAID) if any(j in addons for j in e["jar_ids"])]
    return found + [name for jar, name in OTHERS.items() if jar in addons]


async def read_prices(ctx: ServerContext) -> None:
    """Read the prices Spiget has, at most once a day. Quiet when it can't
    be read: the last price read stays, with its day."""
    saved = dict(ctx.core.db.get_setting(PRICES_SETTING) or {})
    changed = False
    for entry in PAID:
        resource = entry.get("spiget")
        last = saved.get(entry["id"]) or {}
        if not resource or time.time() - float(last.get("tried_at") or 0) < READ_EVERY:
            continue
        price = None
        try:
            answer = downloads.fetch_json(SPIGET.format(resource), 1024 * 1024)
            price = _price(await asyncio.wait_for(answer, 10))  # never hold the picker long
        except (downloads.DownloadError, TimeoutError):
            log.info("couldn't read %s's price from Spiget", entry["name"], exc_info=True)
        saved[entry["id"]] = {**last, "tried_at": time.time()}
        if price:
            saved[entry["id"]].update(price=price, read_at=time.time())
        changed = True
    if changed:
        ctx.core.db.set_setting(PRICES_SETTING, saved)


def _price(answer: Any) -> str | None:
    """ "19.99 USD" from Spiget's answer, or None when it isn't a clear price."""
    if not isinstance(answer, dict) or answer.get("premium") is not True:
        return None
    price, currency = answer.get("price"), str(answer.get("currency") or "")
    if (
        isinstance(price, bool)
        or not isinstance(price, int | float)
        or not _CURRENCY.match(currency)
    ):
        return None
    if not 0 < price < 10_000:
        return None
    return f"{price:.2f} {currency.upper()}"


def _day(ts: float) -> str:
    day = dt.datetime.fromtimestamp(ts, dt.UTC)
    return f"{day.day} {day:%B %Y}"


def _players_mods(ctx: ServerContext) -> bool:
    """Whether a turned-on mod is marked Players or Both (Mods page marks)."""
    from .mods import sides

    ours = {j for e in FREE for j in e["jar_ids"]}
    pack, marks = sides.pack_sides(ctx), sides.read_marks(ctx.config)
    return any(
        sides.mark(ctx.mods, mod, pack, marks)[0] in ("players", "both")
        for mod in ctx.mods.scan(use_cache=True)
        if mod.enabled and mod.mod_id.lower() not in ours
    )


def picker(ctx: ServerContext, held: frozenset[str]) -> dict[str, Any]:
    """What the picker shows this account. Reads the mods folder, so call it
    off the event loop."""
    from . import whitelist

    type_id = ctx.config.server_type.id
    if not supported(type_id):
        return {"supported": False, "installed": [], "free": [], "paid": []}
    addons = whitelist.installed(ctx)
    warn = type_id == "fabric" and _players_mods(ctx)
    free = [
        {
            "id": e["id"],
            "name": e["name"],
            "project": e["project"],
            "on": any(j in addons for j in e["jar_ids"]),
            "warning": warn,
        }
        for e in FREE
        if type_id in e["types"]
    ]
    read = ctx.core.db.get_setting(PRICES_SETTING) or {}
    paid = []
    for e in PAID:
        if type_id not in e["types"]:
            continue
        found = read.get(e["id"]) or {}
        price, checked = e["price"], PRICES_CHECKED if e["price"] else None
        if found.get("price") and found.get("read_at"):
            price, checked = found["price"], _day(float(found["read_at"]))
        paid.append(
            {
                "id": e["id"],
                "name": e["name"],
                "on": any(j in addons for j in e["jar_ids"]),
                "link": e["link"],
                "setup": e["setup"],
                "price": price,
                "checked": checked,
            }
        )
    return {
        "supported": True,
        "installed": installed(addons),
        "free": free,
        "paid": paid,
        "may_install": P.MODS_MANAGE in held,
        "may_add_own_jar": P.MODS_OWN_JAR in held,
    }
