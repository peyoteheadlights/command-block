"""The Store: a Tebex store, set up with the owner's help.

Command Block never touches money or card details. Tebex hosts the store
page and takes the payments; its official plugin pulls the purchases over
outgoing HTTPS and runs each package's commands in the console, so no
port is opened. Tebex has no API to make packages, so the owner makes the
account, payouts and packages on Tebex's site from a copy-paste checklist
this module writes.

What the app does (owner only, a community server in Technical, never a
kids or Test server, only Paper, Purpur, Leaf and Fabric):

  * checks the Game Server secret key with Tebex's Game Server API, keeps
    it encrypted (DPAPI, ``store-keys.bin``) and never logs or answers it;
  * installs Tebex's plugin and LuckPerms from Modrinth (both official
    there), writes the key into Tebex's own config file and names its buy
    command ``store``, with a backup and one Undo;
  * makes three cosmetic Store ranks: LuckPerms groups with a prefix and
    name colour. A new LuckPerms keeps its data in files (``json``), so the
    groups are files written here and the agent can read back who holds a
    rank (the Discord role sync) without parsing the console;
  * "Check my store" reads the packages back (read only) and ticks the
    ones that match a Store rank.

Like a Java class of static helpers: the routes and the Discord bot call
these functions with the server's context.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import yaml

from . import downloads
from .mods.updateall import folder_in_server
from .safechange import SafeChange, run_safe_change
from .security.records import read_secrets, write_secrets

if TYPE_CHECKING:
    from .core import ServerContext
    from .jobs import JobHandle

log = logging.getLogger("msc.store")

API = "https://plugin.tebex.io"
KEY_FILE = "store-keys.bin"
SETTING = "store"  # server id -> what the last check of the store found
TYPES = ("paper", "purpur", "leaf", "fabric")
# Modrinth projects, both published there by their own teams.
TEBEX, LUCKPERMS = "tebex", "luckperms"
BUY_COMMAND = "store"  # in game, /store is the plugin's /buy
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,100}$")
# The template: three cosmetic ranks (Mark can rename them). The colour is
# a Minecraft colour code; the prefix ends with the name colour.
RANKS: tuple[dict[str, Any], ...] = (
    {"id": "supporter", "name": "Supporter", "colour": "&a", "weight": 10},
    {"id": "patron", "name": "Patron", "colour": "&b", "weight": 20},
    {"id": "legend", "name": "Legend", "colour": "&6", "weight": 30},
)
NEVER_SOLD = (
    "Capes, or anything that looks like a cape",
    "Paid random rewards: crate keys, loot boxes, mystery boxes",
    "In-game money or items that can be cashed out for real money",
)
GAMEPLAY_WARNING = (
    "Mojang only allows selling things that don't give a player an advantage over "
    "others or ruin their game. Selling this may break Mojang's rules for servers. "
    "Keep it to looks (cosmetics, titles, ranks) if you're not sure."
)
FEES = "Tebex keeps 5% of each sale, plus the payment company's and payout fees."
DISCLAIMER = "NOT AN OFFICIAL MINECRAFT PRODUCT/SERVICE. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT."
OFFLINE_WARNING = (
    "Bedrock players join this server through Geyser, so the store has to be Tebex's "
    '"Minecraft: Offline" type. Tebex doesn\'t check usernames on that type: a buyer '
    "could type someone else's name."
)


class StoreError(RuntimeError):
    """The message is written for the owner."""


# ------------------------------------------------------------------ who gets one
def not_offered(ctx: ServerContext) -> str | None:
    """Why this server never gets a store (the apps show no Store tab), or
    None when it may."""
    server = ctx.config.server
    if ctx.config.server_type.edition != "java":
        return "Bedrock servers can't have a store."
    if server.test:
        return "A Test server never gets a store."
    if server.played_by == "kids":
        return "A server for kids never gets a store."
    if server.played_by != "community":
        return "The store is only for servers made for a community."
    return None


def fits_type(ctx: ServerContext) -> bool:
    kind = ctx.config.server_type
    return kind.id in TYPES or (getattr(kind, "like", None) or "") in TYPES


def _paper(ctx: ServerContext) -> bool:
    return ctx.config.server_type.content == "plugins"


# ------------------------------------------------------------------ files
# A plugin keeps its files in plugins/<its name>; on Fabric, Tebex uses
# mods/tebex, and LuckPerms config/luckperms and mods/luckperms.
def tebex_config(ctx: ServerContext) -> Path:
    if _paper(ctx):
        return ctx.config.mods_dir / "Tebex" / "config.yml"
    return ctx.config.server_dir / "mods" / "tebex" / "config.yml"


def luckperms_config(ctx: ServerContext) -> Path:
    if _paper(ctx):
        return ctx.config.mods_dir / "LuckPerms" / "config.yml"
    return ctx.config.server_dir / "config" / "luckperms" / "luckperms.conf"


def luckperms_data(ctx: ServerContext) -> Path:
    if _paper(ctx):
        return ctx.config.mods_dir / "LuckPerms" / "json-storage"
    return ctx.config.server_dir / "mods" / "luckperms" / "json-storage"


def storage(ctx: ServerContext) -> str | None:
    """How LuckPerms keeps its data here: "json", another method, or None
    before LuckPerms first started (it makes its config then)."""
    try:
        words = luckperms_config(ctx).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = re.search(r"^\s*storage-method\s*[:=]\s*\"?([\w-]+)", words, re.MULTILINE)
    return found.group(1).lower() if found else "h2"  # LuckPerms' default


def installed(ctx: ServerContext) -> dict[str, bool]:
    here = {m.mod_id.lower() for m in ctx.mods.scan()}
    return {TEBEX: TEBEX in here, LUCKPERMS: LUCKPERMS in here}


def _group_file(ctx: ServerContext, rank: dict[str, Any]) -> Path:
    return luckperms_data(ctx) / "groups" / f"{rank['id']}.json"


def prefix(rank: dict[str, Any]) -> str:
    return f"{rank['colour']}[{rank['name']}] &f"


def make_ranks(ctx: ServerContext) -> list[str]:
    """Write the Store ranks LuckPerms doesn't have yet, as its own group
    files. A group the owner already has is never overwritten."""
    made = []
    for rank in RANKS:
        path = _group_file(ctx, rank)
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "name": rank["id"],
            "permissions": [{"permission": f"weight.{rank['weight']}", "value": True}],
            "prefixes": [{"prefix": prefix(rank), "priority": rank["weight"]}],
        }
        path.write_text(json.dumps(body, indent=2), encoding="utf-8")
        made.append(rank["id"])
    return made


def holders(ctx: ServerContext) -> dict[str, set[str]]:
    """Player UUID -> the Store ranks they hold now, read from LuckPerms'
    user files. Empty when LuckPerms doesn't keep its data in files."""
    if storage(ctx) != "json":
        return {}
    ours = {r["id"] for r in RANKS}
    now = time.time()
    found: dict[str, set[str]] = {}
    for path in (luckperms_data(ctx) / "users").glob("*.json"):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for parent in body.get("parents") or [] if isinstance(body, dict) else []:
            if not isinstance(parent, dict) or parent.get("group") not in ours:
                continue
            if parent.get("context") or float(parent.get("expiry") or now + 1) <= now:
                continue  # only on one server or world, or run out
            found.setdefault(str(body.get("uuid") or path.stem), set()).add(parent["group"])
    return found


def write_tebex_config(ctx: ServerContext, key: str) -> None:
    """The key and the buy command's name in Tebex's own config file. The
    plugin reads the key from there; it's the plugin's design to keep it in
    that file."""
    path = tebex_config(ctx)
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        body = {}
    except (OSError, yaml.YAMLError) as exc:
        raise StoreError(f"Tebex's config file couldn't be read: {exc}") from exc
    if not isinstance(body, dict):
        body = {}
    body.setdefault("buy-command", {}).update({"enabled": True, "name": BUY_COMMAND})
    body.setdefault("server", {}).update({"proxy": False, "secret-key": key})
    body.setdefault("config-version", 2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")


def write_luckperms_config(ctx: ServerContext) -> bool:
    """A new LuckPerms keeps its data in files, so the ranks can be written
    and read here. An existing LuckPerms is never switched over (its ranks
    would be lost)."""
    path = luckperms_config(ctx)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    line = 'storage-method = "json"' if path.suffix == ".conf" else "storage-method: json"
    path.write_text(line + "\n", encoding="utf-8")
    return True


# ------------------------------------------------------------------ the key
def _key_path(ctx: ServerContext) -> Path:
    return Path(ctx.core.config.data_dir) / KEY_FILE


def key_of(ctx: ServerContext) -> str | None:
    return read_secrets(_key_path(ctx)).get(ctx.server_id)


def _save_key(ctx: ServerContext, key: str) -> None:
    keys = read_secrets(_key_path(ctx))
    keys[ctx.server_id] = key
    write_secrets(_key_path(ctx), keys)


async def _tebex(path: str, key: str) -> Any:
    try:
        return await downloads.fetch_json(
            API + path, max_bytes=2 * 1024 * 1024, headers={"X-Tebex-Secret": key}
        )
    except downloads.DownloadError as exc:
        if exc.status in (401, 403, 404):
            raise StoreError(
                "Tebex refused that key. Copy the secret key again from Tebex's site: "
                "Integrations, then Game Servers."
            ) from None
        raise StoreError(str(exc)) from None


def _link(domain: Any) -> str | None:
    """The store page, only as an https address on its own host."""
    words = str(domain or "").strip()
    parsed = urlparse(words if "://" in words else f"https://{words}")
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host):
        return None
    return f"https://{host}"


def _facts(answer: Any) -> dict[str, Any]:
    if not isinstance(answer, dict) or not isinstance(answer.get("account"), dict):
        raise StoreError("Tebex sent an answer that couldn't be read.")
    account = answer["account"]
    return {
        "name": str(account.get("name") or "")[:100],
        "link": _link(account.get("domain")),
        "currency": str((account.get("currency") or {}).get("iso_4217") or "")[:5],
        "offline": not account.get("online_mode", True),
        "checked_at": time.time(),
    }


def saved(ctx: ServerContext) -> dict[str, Any]:
    return (ctx.core.db.get_setting(SETTING, {}) or {}).get(ctx.server_id) or {}


def _remember(ctx: ServerContext, **changes: Any) -> dict[str, Any]:
    everything = ctx.core.db.get_setting(SETTING, {}) or {}
    everything[ctx.server_id] = {**(everything.get(ctx.server_id) or {}), **changes}
    ctx.core.db.set_setting(SETTING, everything)
    return everything[ctx.server_id]


def require_store(ctx: ServerContext) -> None:
    reason = not_offered(ctx)
    if reason:
        raise StoreError(reason)
    if not fits_type(ctx):
        raise StoreError("The store needs a Paper or Fabric server. Change server type first.")


# ------------------------------------------------------------------ setting up
async def connect(ctx: ServerContext, key: str, user: str) -> dict[str, Any]:
    """Check the key with Tebex, keep it, then install the plugin and
    LuckPerms, write Tebex's config and make the Store ranks as one change
    (backup first, one Undo). The server starts again if it was running."""
    require_store(ctx)
    key = (key or "").strip()
    if not KEY_PATTERN.fullmatch(key):
        raise StoreError("That doesn't look like a Tebex secret key.")
    facts = _facts(await _tebex("/information", key))
    if ctx.config.server.crossplay and not facts["offline"]:
        raise StoreError(OFFLINE_WARNING)
    _save_key(ctx, key)
    _remember(ctx, **facts)
    ctx.require_free()
    was_running = ctx.server.running
    title = "Set up the store"

    async def change(job: JobHandle | None) -> dict[str, Any]:
        added = []
        for project, here in installed(ctx).items():
            if not here:
                await ctx.mods.install_from_site(project, user, install_dependencies=True)
                added.append(project)
        write_tebex_config(ctx, key)
        write_luckperms_config(ctx)
        made = make_ranks(ctx) if storage(ctx) == "json" else []
        return {"added": added, "ranks_made": made}

    async def check(result: dict[str, Any]) -> tuple[bool, str]:
        if not all(installed(ctx).values()):
            return False, "Tebex or LuckPerms isn't in the folder"
        return True, "Tebex and LuckPerms are in place"

    # A backup holds only folders inside the server folder, and an empty
    # folder has nothing to put back: then there's no Undo (as with adding
    # a file, agent/mods/addfrom.py).
    top = ctx.config.server_dir
    folders = [
        f
        for f in (folder_in_server(ctx, ctx.config.mods_dir), None if _paper(ctx) else "config")
        if f
    ]
    has_files = any(p.is_file() for f in folders for p in (top / f).rglob("*"))
    plan = SafeChange(
        title=title,
        change=change,
        check=check,
        stop_server=True,
        start_after=was_running,
        take_backup=has_files,
        backup_name="before-store",
        backup_note=f"Before {title}",
        backup_includes=folders,
    )

    async def run(job: JobHandle | None) -> dict[str, Any]:
        return await run_safe_change(ctx.server, ctx.backups, plan, job=job, user=user)

    _, outcome = await ctx.core.jobs.run(
        "store", f"{title} on {ctx.name}", run, server_id=ctx.server_id, risky=True, user=user
    )
    return {"outcome": outcome, "undo": has_files, **view(ctx)}


async def check_store(ctx: ServerContext) -> dict[str, Any]:
    """Read the store and its packages back from Tebex and tick the ones
    that match a Store rank. Packages the app didn't make are listed, with
    a reminder the first time each is seen."""
    require_store(ctx)
    key = key_of(ctx)
    if not key:
        raise StoreError("Connect the store first: paste its secret key.")
    facts = _facts(await _tebex("/information", key))
    answer = await _tebex("/packages", key)
    if not isinstance(answer, list):
        raise StoreError("Tebex sent an answer that couldn't be read.")
    names = {r["name"].lower(): r["id"] for r in RANKS}
    packages = []
    for item in answer[:200]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")[:100]
        packages.append(
            {
                "id": str(item.get("id") or ""),
                "name": name,
                "price": item.get("price") if isinstance(item.get("price"), int | float) else None,
                "type": str(item.get("type") or "")[:20],
                "disabled": bool(item.get("disabled")),
                "rank": names.get(name.strip().lower()),
            }
        )
    seen = set(saved(ctx).get("reminded") or [])
    remind = [p["name"] for p in packages if not p["rank"] and p["id"] not in seen]
    _remember(
        ctx,
        **facts,
        packages=packages,
        reminded=sorted(seen | {p["id"] for p in packages if not p["rank"]}),
    )
    return {**view(ctx), "remind": remind}


# ------------------------------------------------------------------ what the apps show
def checklist(ctx: ServerContext, offline: bool) -> str:
    """The copy-paste steps for Tebex's site. No prices: the owner decides."""
    who = "{username}" if offline else "{id}"
    store_type = "Minecraft: Offline" if offline else "Minecraft: Java Edition"
    lines = [
        "1. Make a Tebex account at https://creator.tebex.io (you must be 18 or older; "
        "Tebex checks your ID before paying out).",
        f'2. Make a project with the store type "{store_type}". It can\'t be changed later.',
        "3. Set up payouts (PayPal or bank) in Tebex's settings.",
        '4. Make one category, for example "Ranks", then one package per rank below. '
        "Pick One-time or Subscription (monthly) yourself, turn gifting on, and set your "
        "own price.",
    ]
    for rank in RANKS:
        add = f"lp user {who} parent add {rank['id']}"
        remove = f"lp user {who} parent remove {rank['id']}"
        lines += [
            f'   Package "{rank["name"]}" (shows as {prefix(rank)}in chat):',
            f"     When bought (Initial): {add}",
            f"     On expiry: {remove}",
            f"     On refund: {remove}",
            f"     On chargeback: {remove}",
        ]
    lines += [
        '   Type the commands without a / at the start. Keep "Require the player to be '
        'online" ticked.',
        "5. Never sell: " + "; ".join(NEVER_SOLD) + ".",
        "6. Adding your own package? Ask: does it change gameplay? If yes: " + GAMEPLAY_WARNING,
        "7. Test each package with Tebex's manual payment, never with real money.",
        "8. On Tebex's Integrations, Game Servers page, copy the secret key and paste it "
        "into Command Block.",
        f"9. {FEES}",
        "",
        "Homepage text for your store page (fill in your contact):",
        f"Welcome to the {ctx.name} store! Everything here only changes how you look.",
        "Questions or refunds: (your email or Discord name)",
        DISCLAIMER,
        "",
        f"Store colour: {ctx.color}" if ctx.color else "",
    ]
    icon = ctx.config.server_dir / "server-icon.png"
    if icon.is_file():
        lines.append(f"Logo: {icon}")
    return "\n".join(line for line in lines if line is not None).rstrip() + "\n"


def view(ctx: ServerContext) -> dict[str, Any]:
    """The Store tab. Never holds the key."""
    reason = not_offered(ctx)
    facts = saved(ctx)
    offline = bool(facts.get("offline")) or bool(ctx.config.server.crossplay)
    here = installed(ctx) if not reason and fits_type(ctx) else {TEBEX: False, LUCKPERMS: False}
    method = storage(ctx)
    made = {r["id"] for r in RANKS if _group_file(ctx, r).exists()} if method == "json" else set()
    return {
        "offered": reason is None,
        "reason": reason,
        "needs_type": reason is None and not fits_type(ctx),
        "geyser": bool(ctx.config.server.crossplay),
        "offline_warning": OFFLINE_WARNING if ctx.config.server.crossplay else None,
        "key_saved": bool(key_of(ctx)),
        "installed": here,
        "connected": bool(key_of(ctx)) and all(here.values()),
        "luckperms_storage": method,
        # An existing LuckPerms that keeps its data elsewhere: the ranks are
        # made by hand with the commands in the checklist.
        "ranks_by_hand": method not in (None, "json"),
        "ranks": [{**r, "prefix": prefix(r), "made": r["id"] in made} for r in RANKS],
        "store": {k: facts.get(k) for k in ("name", "link", "currency", "offline", "checked_at")},
        "packages": facts.get("packages"),
        "buy_command": f"/{BUY_COMMAND}",
        "checklist": checklist(ctx, offline),
        "never_sold": list(NEVER_SOLD),
        "gameplay_warning": GAMEPLAY_WARNING,
        "fees": FEES,
        "open_tebex": "https://creator.tebex.io",
    }


def link_of(ctx: ServerContext) -> str | None:
    """The store page for Discord's /store, once a check found it."""
    if not_offered(ctx) or not key_of(ctx):
        return None
    return saved(ctx).get("link")
