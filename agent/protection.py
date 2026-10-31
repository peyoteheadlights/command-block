"""The Protection card: how one server is set up, read from its own files.

Think of it as a read-out, not a test. ``facts()`` reads what the server's
files and this PC say (server.properties, ops.json, Paper's settings files,
the add-ons that are on, Tailscale), ``lines()`` turns those facts into the
card's lines, and nothing in between guesses (house rule 1). ``lines()`` is
a plain function of the facts, so every rule can be tested without a server.

Each line has a level: "worst" (a combination of settings that lets anyone
in), "problem", "suggest" or "info", and the worst ones go first. A line
with a problem can carry a fix: one click, run as a safe change with one
backup and one Undo (agent/gamesettings.py), needing the permission the
fix names. The card never gives a verdict: the words "safe" and "clean"
never appear, and "protected" only in "Not protected"
(tests/test_protection.py reads every line's words to check).

Also here: the router port hooks (open the port while the server runs,
which UPnP fills in later), "Stop and close the port", Mojang's
Log4Shell fix and the "Check this PC for fractureiser" check.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import downloads, gamesettings, notes
from .minecraft.properties import FILENAME, FormError, PropertiesFile
from .security import permissions as P

if TYPE_CHECKING:
    from .core import ServerContext

log = logging.getLogger("msc.protection")

LEVELS = ("worst", "problem", "suggest", "info")
# The add-ons that make players log in with a password on an offline-mode
# server, by their jar's id (research/login-antibot-addons.md).
LOGIN_ADDONS = {"authme": "AuthMe", "easyauth": "EasyAuth", "nedologin": "Nedologin"}
LOGIN_ADDONS_PREFIX = "loginsystem"  # Login System's id starts with this
ANTIBOT_ADDONS = {"sonar": "Sonar"}
# Names from No-IP's free list: they point straight at the home address.
NO_IP = (
    "ddns.net",
    "hopto.org",
    "zapto.org",
    "sytes.net",
    "no-ip.org",
    "no-ip.biz",
    "no-ip.info",
    "servegame.com",
    "serveminecraft.net",
    "myftp.org",
)
PLAYIT = "https://playit.gg"

# Paper's own protections, by file and key, with the value the fix writes.
BUKKIT = "bukkit.yml"
PAPER_GLOBAL = "config/paper-global.yml"
PAPER_WORLD = "config/paper-world-defaults.yml"
THROTTLE = (BUKKIT, "settings.connection-throttle", 4000)
PACKET_LIMIT = (PAPER_GLOBAL, "packet-limiter.all-packets.max-packet-rate", 500.0)
ANTI_XRAY = (PAPER_WORLD, "anticheat.anti-xray.enabled", True)
DUPES = tuple(
    (PAPER_GLOBAL, f"unsupported-settings.{key}", False)
    for key in (
        "allow-piston-duplication",
        "allow-permanent-block-break-exploits",
        "allow-headless-pistons",
    )
)

# Mojang's own Log4Shell fix by Minecraft version (minecraft.net, "Important
# message: security vulnerability Java edition", 2021-12-10): a launch flag,
# and for 1.7 to 1.16.5 a settings file from Mojang's site, checked by its
# sha1 (which is also in the link). Below 1.7 isn't affected; 1.18.1 and
# newer are fixed.
LOG4J = (
    ((1, 7, 0), (1, 11, 2), "log4j2_17-111.xml", "4bb89a97a66f350bc9f73b3ca8509632682aea2e"),
    ((1, 12, 0), (1, 16, 5), "log4j2_112-116.xml", "02937d122c86ce73319ef9975b58896fc1b491d1"),
    ((1, 17, 0), (1, 18, 0), None, None),
)
NO_LOOKUPS = "-Dlog4j2.formatMsgNoLookups=true"
LOG4J_LINK = (
    "https://www.minecraft.net/en-us/article/important-message--security-vulnerability-java-edition"
)
_MC = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")


class ProtectionError(RuntimeError):
    pass


# ---------------------------------------------------------------- the router port
@dataclass
class RouterHooks:
    """The router port while the server runs. UPnP fills these
    in; until then they are None and the hooks do nothing. ``open`` decides
    itself whether "Let friends join from the internet" is on."""

    open: Callable[[ServerContext], Awaitable[bool]] | None = None
    close: Callable[[ServerContext], Awaitable[None]] | None = None
    outside_test: Callable[[ServerContext], Awaitable[Any]] | None = None
    mapped: Callable[[ServerContext], bool | None] | None = None


ROUTER = RouterHooks()


async def router_open(ctx: ServerContext) -> None:
    """On start: open the router port, then test it from outside."""
    try:
        if ROUTER.open and await ROUTER.open(ctx) and ROUTER.outside_test:
            await ROUTER.outside_test(ctx)
    except Exception:  # a router that won't answer must never stop a start
        log.exception("could not open the router port for %s", ctx.server_id)


async def router_close(ctx: ServerContext) -> None:
    """On stop, and when "Let friends join from the internet" is turned off."""
    try:
        if ROUTER.close:
            await ROUTER.close(ctx)
    except Exception:
        log.exception("could not close the router port for %s", ctx.server_id)


async def stop_and_close(ctx: ServerContext, user: str) -> dict[str, Any]:
    """The big-attack button: stop the server and take the router port away."""
    from .minecraft.process import ServerError

    stopped = False
    if ctx.server.running:
        try:
            await ctx.server.stop(actor=user)
            stopped = True
        except ServerError as exc:
            raise ProtectionError(str(exc)) from exc
    await router_close(ctx)
    ctx.db.audit("stop_and_close", user=user, target=ctx.server_id)
    return {"stopped": stopped, "router": ROUTER.close is not None}


# ---------------------------------------------------------------- facts
@dataclass
class Facts:
    """Everything the card's rules look at. None means not known."""

    edition: str = "java"
    type_id: str = ""
    plugins: bool = False  # a Paper-type server (Bukkit plugins)
    properties: dict[str, str] | None = None  # None: no server.properties yet
    port: int = 25565
    default_port: int = 25565
    suggested_port: int | None = None
    ops: list[str] | None = None
    addons: set[str] = field(default_factory=set)  # add-ons that are on, by id
    home: bool = False
    tailscale: bool | None = None
    router: bool | None = None  # None: this app doesn't open router ports yet
    played_by: str = ""
    shared_address: str = ""
    minecraft: str | None = None
    jvm_args: list[str] = field(default_factory=list)
    paper: dict[tuple[str, str], Any] | None = None  # (file, key) -> value; None: not read
    login: list[dict[str, Any]] = field(default_factory=list)  # login add-ons for the type
    antibot: list[dict[str, Any]] = field(default_factory=list)
    spam: dict[str, Any] = field(default_factory=dict)
    join: dict[str, Any] | None = None  # Join protection's status (agent/joinguard.py)
    join_burst_seen: bool = False  # 15+ new names in one minute within 7 days
    proxy: list[dict[str, Any]] = field(default_factory=list)  # proxy add-ons for the type

    def prop(self, key: str, default: str) -> str:
        return ((self.properties or {}).get(key) or default).strip().lower()

    @property
    def whitelist_key(self) -> str:
        return "allow-list" if self.edition == "bedrock" else "white-list"


def facts(ctx: ServerContext) -> Facts:
    """Read the facts for one server. Asks Tailscale, so call it off the event loop."""
    from . import joininfo, whitelist
    from .discord import layout
    from .minecraft.playeractions import read_list

    server_type = ctx.config.server_type
    directory = Path(ctx.config.server_dir)
    path = directory / FILENAME
    try:
        values = PropertiesFile.read(path).values() if path.is_file() else None
    except (OSError, ValueError):
        values = None
    port = ctx.core.ports.port_of(ctx)
    plugins = server_type.content == "plugins" and server_type.changes_farms
    paper = None
    if plugins:
        paper = {
            (name, key): gamesettings.read_yaml(directory / name, key)
            for name, key, _ in (THROTTLE, PACKET_LIMIT, ANTI_XRAY, *DUPES)
            if (directory / name).is_file()
        }
    offered = [e for e in notes.RECOMMENDED if server_type.id in e["types"]]
    ops = read_list(directory, "ops")["players"] if server_type.dialect == "java" else None
    security = ctx.core.config.security
    return Facts(
        edition=server_type.edition,
        type_id=server_type.id,
        plugins=plugins,
        properties=values,
        port=port.port,
        default_port=server_type.default_port,
        suggested_port=ctx.core.ports.suggest(server_type.game_protocol, port.port + 1)
        if port.port == server_type.default_port
        else None,
        ops=[o["name"] for o in ops] if ops is not None else None,
        addons=whitelist.installed(ctx),
        home=bool(joininfo.local_addresses()),
        tailscale=bool(joininfo.tailscale_address().get("connected")),
        router=ROUTER.mapped(ctx) if ROUTER.mapped else None,
        played_by=ctx.config.server.played_by,
        shared_address=layout.addresses(ctx.core.db).get(ctx.server_id) or "",
        minecraft=ctx.server.mc_version,
        jvm_args=list(ctx.config.server.jvm_args),
        paper=paper,
        login=[_addon(e) for e in offered if e.get("role") == "login"],
        antibot=[_addon(e) for e in offered if e.get("role") == "antibot"],
        spam={
            "on": security.chat_spam_rule,
            "names": security.chat_spam_names,
            "seconds": int(security.chat_spam_seconds),
        },
        join=ctx.core.guard.status(ctx),
        join_burst_seen=_burst_seen(ctx),
        proxy=[_addon(e) for e in offered if e.get("role") == "proxy"],
    )


def _burst_seen(ctx: ServerContext) -> bool:
    """Whether 15 or more names new to this server joined within one minute
    in the last 7 days (from the join history, so measured even while Join
    protection is off)."""
    import time

    from .joinguard import PRESETS

    row = ctx.db.query_one(
        "SELECT COUNT(*) AS n FROM players WHERE server_id = ? AND first_seen > ? "
        "GROUP BY CAST(first_seen / 60 AS INTEGER) ORDER BY n DESC LIMIT 1",
        (ctx.server_id, time.time() - 7 * 86400),
    )
    return bool(row and row["n"] >= PRESETS["small"]["new_names"])


def _addon(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": entry["project"],
        "name": entry["name"],
        "small": entry.get("technical_only", False),
    }


# ---------------------------------------------------------------- the rules
def _line(
    line_id: str,
    level: str,
    fix: str | None = None,
    technical: bool = False,
    **values: Any,
) -> dict[str, Any]:
    """One line of the card. ``words`` is its key in shared/strings.json."""
    return {
        "id": line_id,
        "level": level,
        "words": f"protect.{line_id}",
        "values": {k: v for k, v in values.items() if k not in ("addons", "link")},
        "fix": fix,
        "permission": FIXES[fix][0] if fix else None,
        "addons": values.get("addons") or [],
        "link": values.get("link"),
        "technical": technical,
    }


def mc_version(text: str | None) -> tuple[int, int, int] | None:
    found = _MC.match(text or "")
    return (int(found[1]), int(found[2]), int(found[3] or 0)) if found else None


def log4j_row(minecraft: str | None) -> tuple | None:
    """Mojang's fix for this Minecraft version, or None when it isn't affected."""
    version = mc_version(minecraft)
    if version is None:
        return None
    return next((row for row in LOG4J if row[0] <= version <= row[1]), None)


def log4j_flag(row: tuple) -> str:
    return f"-Dlog4j.configurationFile={row[2]}" if row[2] else NO_LOOKUPS


def lines(f: Facts) -> list[dict[str, Any]]:
    """The card for these facts, worst first."""
    if f.properties is None:
        return [_line("not_started", "info")]
    found: list[dict[str, Any]] = []
    java = f.edition == "java"
    offline = f.prop("online-mode", "true") == "false"
    whitelist_on = f.prop(f.whitelist_key, "false") == "true"
    login = next((n for k, n in LOGIN_ADDONS.items() if k in f.addons), None) or next(
        ("Login System" for a in f.addons if a.startswith(LOGIN_ADDONS_PREFIX)), None
    )
    internet = True if f.router else None  # never False: a port forwarded by hand can't be seen

    # The worst combinations first.
    if offline and not whitelist_on and internet is not False:
        found.append(_line("offline_open", "worst", "whitelist_on"))
    if java and offline and f.ops and not login:
        found.append(_line("offline_ops", "worst", None, count=len(f.ops), addons=f.login))
    rcon = java and f.prop("enable-rcon", "false") == "true"
    if rcon:
        found.append(_line("rcon_on", "worst", "rcon_off", port=f.prop("rcon.port", "25575")))

    # Who can join.
    if offline and not login:
        if f.login:
            found.append(_line("not_protected", "problem", None, addons=f.login))
        else:
            found.append(_line("not_protected_none", "problem", "online_on", type=f.type_id))
        if f.antibot and not any(k in f.addons for k in ANTIBOT_ADDONS):
            found.append(_line("antibot", "suggest", None, addons=f.antibot))
        if f.ops:
            found.append(_line("ops_signed_in", "suggest"))
    elif offline:
        found.append(_line("login_on", "info", None, addon=login))
    if not whitelist_on and not any(line["id"] == "offline_open" for line in found):
        community = f.played_by == "community"
        found.append(_line("whitelist_off", "info" if community else "problem", "whitelist_on"))
    elif whitelist_on:
        found.append(_line("whitelist_on", "info"))
    if java and f.prop("enable-query", "false") == "true":
        found.append(
            _line("query_on", "problem", "query_off", port=f.prop("query.port", str(f.port)))
        )

    # The port and how players get in.
    if f.port == f.default_port:
        found.append(
            _line(
                "port_default",
                "suggest",
                "change_port" if f.suggested_port else None,
                port=f.port,
                suggested=f.suggested_port,
            )
        )
    else:
        found.append(_line("port_other", "info", port=f.port))
    found.append(_line("reach_home" if f.home else "reach_no_home", "info"))
    if f.tailscale is not None:
        found.append(_line("reach_tailscale" if f.tailscale else "reach_no_tailscale", "info"))
    router = {True: "reach_router", False: "reach_no_router", None: "reach_router_unknown"}
    found.append(_line(router[f.router], "info"))
    if f.played_by == "community" or f.router:
        found.append(_line("hide_address", "suggest", None, link=PLAYIT))
    host = f.shared_address.split(":")[0].lower().rstrip(".")
    if any(host == d or host.endswith("." + d) for d in NO_IP):
        found.append(_line("no_ip", "problem", None, name=host))

    # Paper's own settings.
    if f.plugins and f.paper is not None:
        found += _paper(f.paper)

    # Log4Shell, only once the console has printed the version.
    row = log4j_row(f.minecraft) if java else None
    if row and log4j_flag(row) in f.jvm_args:
        found.append(_line("log4shell_fixed", "info", version=f.minecraft))
    elif row:
        found.append(_line("log4shell", "worst", "log4shell", version=f.minecraft))

    found += _join(f)
    found.append(_line("big_attacks", "info", "stop_and_close"))
    if java and f.spam.get("on"):
        found.append(_line("chat_spam", "info", technical=True, **f.spam))
    order = {level: i for i, level in enumerate(LEVELS)}
    return sorted(found, key=lambda line: order[line["level"]])


def _join(f: Facts) -> list[dict[str, Any]]:
    """Join protection's lines: suggested when the server is for a community,
    its port is open or a burst of new names was measured."""
    import time

    join = f.join
    if join is None or join["why_not"] == "java_only":
        return []
    if join["why_not"]:
        return [_line("join_start_command", "info", technical=True)]
    if not join["on"]:
        wanted = f.played_by == "community" or bool(f.router) or f.join_burst_seen
        return [_line("join_off", "suggest" if wanted else "info", "join_on", technical=not wanted)]
    if not join["running"]:
        return [_line("join_restart" if join["restart_needed"] else "join_ready", "info")]
    found = [_line("join_on", "info", blocked=join["blocked_today"])]
    if join.get("launch_until"):
        hours = max(1, round((join["launch_until"] - time.time()) / 3600))
        found.append(_line("join_launch", "info", hours=hours))
    if join["real_addresses"]:
        found.append(_line("join_real_addresses", "info", technical=True))
    else:
        found.append(_line("join_vanilla_fallback", "info", technical=True, addons=f.proxy))
    if join["playit_seen"]:
        found.append(_line("join_playit", "info"))
    return found


def _paper(values: dict[tuple[str, str], Any]) -> list[dict[str, Any]]:
    def value(item: tuple) -> Any:
        return values.get((item[0], item[1]))

    if not values:
        return [_line("paper_not_read", "info", technical=True)]
    found = []
    throttle = value(THROTTLE)
    if isinstance(throttle, int) and throttle <= 0:
        found.append(_line("throttle_off", "problem", "throttle_on"))
    elif isinstance(throttle, int):
        found.append(_line("throttle_on", "info", technical=True, ms=throttle))
    rate = value(PACKET_LIMIT)
    if isinstance(rate, (int, float)) and rate <= 0:
        found.append(_line("packet_limit_off", "problem", "packet_limit_on"))
    elif isinstance(rate, (int, float)):
        found.append(_line("packet_limit_on", "info", technical=True, rate=rate))
    xray = value(ANTI_XRAY)
    if xray is False:
        found.append(_line("anti_xray_off", "suggest", "anti_xray_on"))
    elif xray is True:
        found.append(_line("anti_xray_on", "info", technical=True))
    dupes = [item[1].rsplit(".", 1)[1] for item in DUPES if value(item) is True]
    if dupes:
        found.append(_line("dupes_on", "suggest", "dupes_off", keys=", ".join(dupes)))
    return found


# ---------------------------------------------------------------- fixes
def _props(values: dict[str, str], title: str):
    async def apply(ctx: ServerContext, user: str, body: dict[str, Any]) -> dict[str, Any]:
        return await gamesettings.set_values(ctx, values, title, user)

    return apply


async def _whitelist_on(ctx: ServerContext, user: str, body: dict[str, Any]) -> dict[str, Any]:
    key = "allow-list" if ctx.config.server_type.edition == "bedrock" else "white-list"
    result = await gamesettings.set_values(ctx, {key: "true"}, "Turning the whitelist on", user)
    await live_whitelist_on(ctx)
    return result


async def live_whitelist_on(ctx: ServerContext) -> bool:
    """Turn a running server's whitelist on now, with Minecraft's own
    command (Minecraft writes it into server.properties itself)."""
    from .minecraft.commands import validate
    from .minecraft.process import ServerError

    if not ctx.server.running or ctx.server.state.value != "ONLINE":
        return False
    command = "allowlist on" if ctx.config.server_type.edition == "bedrock" else "whitelist on"
    try:
        await ctx.server.send_command(validate(command, confirm=True).raw)
    except ServerError:
        return False
    return True


async def _change_port(ctx: ServerContext, user: str, body: dict[str, Any]) -> dict[str, Any]:
    port = body.get("port") or ctx.core.ports.suggest(
        ctx.config.server_type.game_protocol, ctx.core.ports.port_of(ctx).port + 1
    )
    if not port:
        raise ProtectionError("No free port was found near this one. Pick one in Game settings.")
    return await gamesettings.save(ctx, {"server-port": int(port)}, user=user)


def _yaml(items: tuple, title: str):
    async def apply(ctx: ServerContext, user: str, body: dict[str, Any]) -> dict[str, Any]:
        found: dict[str, list[tuple[str, Any]]] = {}
        for name, key, value in items:
            found.setdefault(name, []).append((key, value))
        result: dict[str, Any] = {}
        for name, pairs in found.items():
            result = await gamesettings.set_yaml(ctx, name, tuple(pairs), title, user)
        return result

    return apply


async def _join_on(ctx: ServerContext, user: str, body: dict[str, Any]) -> dict[str, Any]:
    """Turn Join protection on (Server settings turns it off). It starts
    with the server's next start."""
    ctx.config.set("server.join_protection", True)
    ctx.config.save()
    ctx.db.audit("join_protection_on", user=user, target=ctx.server_id)
    return {"undo": None}


async def _log4shell(ctx: ServerContext, user: str, body: dict[str, Any]) -> dict[str, Any]:
    """Mojang's fix: its settings file next to the server for 1.7 to 1.16.5,
    and the launch flag. Changed the same way as the memory setting, so it is
    taken off in Server settings' Java options (Technical), not by Undo."""
    row = log4j_row(ctx.server.mc_version)
    if row is None:
        raise ProtectionError("This server's Minecraft version doesn't need the Log4Shell fix.")
    if row[2]:
        spec = downloads.FileSpec(
            url=f"https://launcher.mojang.com/v1/objects/{row[3]}/{row[2]}",
            name=row[2],
            sha1=row[3],
            allow_unverified=False,
            max_bytes=64 * 1024,
        )
        await downloads.download(spec, Path(ctx.config.server_dir))
    flag = log4j_flag(row)
    args = list(ctx.config.server.jvm_args)
    if flag not in args:
        ctx.config.set("server.jvm_args", [*args, flag])
        ctx.config.save()
    ctx.db.audit("log4shell_fix", user=user, target=ctx.server_id, detail=flag)
    return {"changed": {"jvm_args": flag}, "restart_needed": ctx.server.running, "undo": None}


async def _stop_and_close(ctx: ServerContext, user: str, body: dict[str, Any]) -> dict[str, Any]:
    return await stop_and_close(ctx, user)


FixFunction = Callable[["ServerContext", str, dict[str, Any]], Awaitable[dict[str, Any]]]
# fix id -> (the permission it needs, what it does)
FIXES: dict[str, tuple[str, FixFunction]] = {
    "whitelist_on": (P.SETTINGS_GAME, _whitelist_on),
    "online_on": (P.SETTINGS_GAME, _props({"online-mode": "true"}, "Turning online mode on")),
    "rcon_off": (P.SETTINGS_GAME, _props({"enable-rcon": "false"}, "Turning RCON off")),
    "query_off": (P.SETTINGS_GAME, _props({"enable-query": "false"}, "Turning query off")),
    "change_port": (P.SETTINGS_GAME, _change_port),
    "throttle_on": (P.SETTINGS_GAME, _yaml((THROTTLE,), "Turning the connection throttle on")),
    "packet_limit_on": (
        P.SETTINGS_GAME,
        _yaml((PACKET_LIMIT,), "Turning the packet limiter on"),
    ),
    "anti_xray_on": (P.SETTINGS_GAME, _yaml((ANTI_XRAY,), "Turning Anti-Xray on")),
    "dupes_off": (P.SETTINGS_GAME, _yaml(DUPES, "Turning duplication tricks off")),
    "log4shell": (P.SETTINGS_SERVER, _log4shell),
    "stop_and_close": (P.SERVER_CONTROL, _stop_and_close),
    "join_on": (P.SETTINGS_SERVER, _join_on),
}


async def card(ctx: ServerContext, held: frozenset[str]) -> dict[str, Any]:
    """The card for one account: a fix it may not use is left off its line."""
    read = await asyncio.to_thread(facts, ctx)
    shown = []
    for line in lines(read):
        if line["fix"] and line["permission"] not in held:
            line = {**line, "fix": None, "permission": None}
        if line["addons"] and P.MODS_MANAGE not in held:
            line = {**line, "addons": []}
        shown.append(line)
    return {"lines": shown, "running": ctx.server.running}


async def fix(
    ctx: ServerContext, fix_id: str, held: frozenset[str], user: str, body: dict[str, Any]
) -> dict[str, Any]:
    if fix_id not in FIXES:
        raise ProtectionError("That fix isn't known.")
    permission, apply = FIXES[fix_id]
    if permission not in held:
        raise ProtectionError("Your account isn't allowed to do that on this server.")
    try:
        return await apply(ctx, user, body)
    except FormError:
        raise
    except downloads.DownloadError as exc:
        raise ProtectionError(f"Mojang's fix file couldn't be downloaded: {exc}") from exc


# ---------------------------------------------------------------- fractureiser
def fractureiser_check(
    home: Path | None = None, local: Path | None = None, run_key: dict[str, str] | None = None
) -> dict[str, list[str]]:
    """The known fractureiser markers on this PC (its community repo,
    docs/users.md and tech.md): a "Microsoft Edge" folder (with a space) in
    the local app data folder, its jar, a Startup-folder or Run-key entry
    that starts it, and the Linux service and jar. Read only."""
    home = home or Path.home()
    local = local or Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    paths = (
        local / "Microsoft Edge",
        local / "Microsoft Edge" / "libWebGL64.jar",
        roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "run.bat",
        home / ".config" / ".data" / "lib.jar",
        home / ".config" / "systemd" / "user" / "systemd-utility.service",
        Path("/etc/systemd/system/systemd-utility.service"),
    )
    found = [str(p) for p in paths if p.exists()]
    values = run_key if run_key is not None else _run_key()
    found += [
        f"Run key: {name}"
        for name, value in values.items()
        if "libwebgl64" in value.lower() or "microsoft edge\\lib" in value.lower()
    ]
    checked = [*map(str, paths), "Run key: " + RUN_KEY]
    return {"found": found, "checked": checked}


RUN_KEY = r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run"


def _run_key() -> dict[str, str]:
    """The current user's Run key, read only (Windows)."""
    try:
        import winreg
    except ImportError:
        return {}
    found: dict[str, str] = {}
    try:
        with winreg.OpenKey(  # type: ignore[attr-defined]
            winreg.HKEY_CURRENT_USER,  # type: ignore[attr-defined]
            RUN_KEY.split("\\", 1)[1],
        ) as key:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)  # type: ignore[attr-defined]
                except OSError:
                    break
                found[str(name)] = str(value)
                i += 1
    except OSError:
        pass
    return found


# ---------------------------------------------------------------- look-alike names
def _plain(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _distance(a: str, b: str) -> int:
    """Edit distance (Levenshtein), small strings only."""
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def flag_look_alikes(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark a search result whose name is one or two letters from a far
    more downloaded result's name in the same list (a typosquat). Only a
    flag: the person decides."""
    for hit in hits:
        mine = _plain(hit.get("title") or "")
        downloads_ = hit.get("downloads") or 0
        if len(mine) < 4:
            continue
        for other in hits:
            theirs = _plain(other.get("title") or "")
            if (
                other is hit
                or not theirs
                or (other.get("downloads") or 0) < 10 * max(1, downloads_)
            ):
                continue
            if _distance(mine, theirs) <= (1 if len(theirs) < 8 else 2):
                hit["look_alike"] = other.get("title")
                break
    return hits
