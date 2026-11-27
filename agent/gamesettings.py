"""The Game settings page: one server's server.properties, edited safely.

Saving goes through the safe-change routine and the job tracker, like every
other risky change:

  1. every value is checked first (agent/minecraft/properties.py), and the
     port with the port manager; one bad value means nothing is written
  2. a verified backup of the old server.properties is taken. It is the
     change's one-click undo on the Backups page
  3. only the changed keys' lines are rewritten; comments and keys this app
     doesn't know stay exactly as they were
  4. the file is read back to check the new values are there

The server is never stopped for this. Minecraft reads server.properties
when it starts, so while it is running the page says the change takes
effect after a restart, and offers one.
"""

from __future__ import annotations

import asyncio
import os
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .events import Event
from .minecraft import properties
from .ports import is_free
from .safechange import SafeChange, run_safe_change

if TYPE_CHECKING:
    from .core import ServerContext
    from .jobs import JobHandle


def port_problem(ctx: ServerContext, port: int) -> str | None:
    """Why this server can't use ``port``, or None. Another server on the
    list using it, or another program holding it now, both count."""
    protocol = ctx.config.server_type.game_protocol
    bedrock = ctx.config.server_type.edition == "bedrock"
    # A Bedrock game port is UDP (RakNet) and TCP (NetherNet's join page).
    protocols = (protocol, "tcp") if bedrock else (protocol,)
    used = ctx.core.ports.used(exclude=ctx.server_id)
    owner = next((used[(p, int(port))] for p in protocols if (p, int(port)) in used), None)
    if owner:
        return (
            f"The server '{ctx.core.servers[owner].name}' already uses port {port}. "
            "Pick another one."
        )
    if bedrock:
        # A Bedrock server also listens on server-portv6, which this page
        # doesn't change; the two can't be the same port.
        from .ports import bedrock_v6_port

        if int(port) == bedrock_v6_port(ctx.config.server_dir).port:
            return (
                f"This server already uses port {port} for IPv6 (server-portv6). Pick another one."
            )
    current = ctx.core.ports.port_of(ctx).port
    if int(port) != current and not all(is_free(int(port), p) for p in protocols):
        return f"Another program on this PC is using port {port}. Pick another one."
    return None


def waiting_for_restart(ctx: ServerContext, modified_at: float | None) -> bool:
    """True when the file was changed after the running server finished
    starting, so what it is using now is not what the file says. Measured
    from the file's own time and the moment the console said "Done".

    Not from the moment the process started: Minecraft rewrites
    server.properties itself early in every start, so that would make every
    running server look as if it were waiting for a restart. While it is
    still starting nothing is claimed."""
    online = ctx.server.online_at
    return bool(
        ctx.server.running
        and ctx.server.state.value == "ONLINE"
        and online
        and modified_at
        and modified_at > online
    )


def view(ctx: ServerContext) -> dict[str, Any]:
    directory = ctx.config.server_dir
    data = properties.form_view(directory, ctx.config.server_type)
    return {
        **data,
        "running": ctx.server.running,
        "restart_needed": waiting_for_restart(ctx, data["modified_at"]),
        "icon": None if _bedrock(ctx) else (directory / ICON).is_file(),
    }


# ----------------------------------------------------------------- server icon
# The picture Java servers show next to their name in the server list. The
# apps resize the picked picture to 64 x 64 and send a PNG; Minecraft only
# reads it when the server starts, and ignores any other size.
ICON = "server-icon.png"
ICON_SIZE = 64
ICON_MAX_BYTES = 256 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _bedrock(ctx: ServerContext) -> bool:
    return ctx.config.server_type.edition == "bedrock"


def check_icon(data: bytes) -> None:
    """Raise PropertiesError unless ``data`` is a 64 x 64 PNG. Read from the
    PNG's own header (its first chunk, IHDR, holds the width and height)."""
    if len(data) > ICON_MAX_BYTES:
        raise properties.PropertiesError("The picture is too big. A server icon is a few KB.")
    if not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        raise properties.PropertiesError("This isn't a PNG picture.")
    width, height = struct.unpack(">II", data[16:24])
    if (width, height) != (ICON_SIZE, ICON_SIZE):
        raise properties.PropertiesError(
            f"A server icon has to be {ICON_SIZE} x {ICON_SIZE} pixels. "
            f"This one is {width} x {height}."
        )


def icon_path(ctx: ServerContext) -> Path | None:
    path = ctx.config.server_dir / ICON
    return path if not _bedrock(ctx) and path.is_file() else None


async def save_icon(ctx: ServerContext, data: bytes | None, user: str = "system") -> dict[str, Any]:
    """Put ``data`` in as server-icon.png, or take the icon away (None).
    Through the safe-change routine, so Undo puts the old icon back."""
    if _bedrock(ctx):
        raise properties.PropertiesError("Bedrock servers don't show a server icon.")
    if data is not None:
        check_icon(data)
    path = ctx.config.server_dir / ICON
    title = "Changing the server icon" if data is not None else "Removing the server icon"

    async def change(job: JobHandle | None) -> dict[str, Any]:
        def write() -> None:
            if data is None:
                path.unlink(missing_ok=True)
                return
            temporary = path.with_name(ICON + ".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, path)

        await asyncio.to_thread(write)
        return {}

    async def check(_result: dict[str, Any]) -> tuple[bool, str]:
        now = path.read_bytes() if path.is_file() else None
        return (now == data), ("Read back as saved" if now == data else "Didn't read back")

    plan = SafeChange(
        title=title,
        change=change,
        check=check,
        stop_server=False,
        take_backup=path.is_file(),
        backup_name="pre-server-icon",
        backup_includes=[ICON],
    )

    async def run(job: JobHandle | None) -> dict[str, Any]:
        return await run_safe_change(ctx.server, ctx.backups, plan, job=job, user=user)

    _, outcome = await ctx.core.jobs.run(
        "server_icon", f"{title} on {ctx.name}", run, server_id=ctx.server_id, risky=True, user=user
    )
    return {
        "ok": True,
        "icon": data is not None,
        "undo": outcome.get("undo"),
        "running": ctx.server.running,
    }


async def save(
    ctx: ServerContext,
    updates: dict[str, Any] | None = None,
    raw_text: str | None = None,
    user: str = "system",
) -> dict[str, Any]:
    """Save form values (``updates``) or the whole file as typed in
    Technical mode (``raw_text``). Returns what changed and the undo."""
    directory = ctx.config.server_dir
    path = directory / properties.FILENAME
    server_type = ctx.config.server_type
    known = properties.fields_for(server_type.dialect)
    if raw_text is not None:
        edited = properties.check_raw_text(raw_text, server_type.dialect)
        before = properties.PropertiesFile.read(path).values() if path.is_file() else {}
        after = edited.values()
        port = after.get("server-port")
        if port and port.strip() != (before.get("server-port") or "").strip():
            problem = port_problem(ctx, int(port))
            if problem:
                raise properties.FormError({"server-port": problem})
        seed_before = (before.get("level-seed") or "").strip()
        seed_after = (after.get("level-seed") or "").strip()
        if seed_after != seed_before and properties.world_exists(directory, before, server_type):
            raise properties.FormError(
                {"level-seed": "The world already exists, so its seed can't change."}
            )
        changed = {
            key: value for key, value in after.items() if before.get(key) != value and key in known
        }
        others = sorted(
            key for key in set(after) | set(before) if before.get(key) != after.get(key)
        )
        current_text = properties.PropertiesFile.read(path).text() if path.is_file() else ""
        if edited.text() == current_text:
            return {"changed": {}, "keys": [], "nothing_changed": True, **_after(ctx)}
    else:
        before_file = properties.PropertiesFile.read(path) if path.is_file() else None
        old_port = ((before_file and before_file.get("server-port")) or "").strip()
        edited, changed = properties.apply_form(
            directory, updates or {}, lambda port: port_problem(ctx, port), server_type
        )
        # server-udp-ports set to the game port's number (agent/ports.py
        # set_udp_ports) moves with it; an owner's own value stays.
        new_port = changed.get("server-port")
        if (
            new_port
            and server_type.edition == "bedrock"
            and old_port
            and (edited.get("server-udp-ports") or "").strip() == old_port
            and edited.set("server-udp-ports", str(new_port))
        ):
            changed["server-udp-ports"] = str(new_port)
        others = sorted(changed)
        if not changed:
            return {"changed": {}, "keys": [], "nothing_changed": True, **_after(ctx)}

    return await write_properties(ctx, edited, changed, others, user)


async def set_values(
    ctx: ServerContext, values: dict[str, str], title: str, user: str = "system"
) -> dict[str, Any]:
    """Set server.properties keys the form doesn't show (online-mode,
    enable-rcon...) to fixed values, the same safe way as the form: one
    backup, one Undo. Used by the Protection card's fixes."""
    path = ctx.config.server_dir / properties.FILENAME
    if not path.is_file():
        raise properties.FormError(
            {properties.FILENAME: "server.properties isn't there yet. Start the server once."}
        )
    edited = properties.PropertiesFile.read(path)
    changed = {key: value for key, value in values.items() if edited.set(key, value)}
    if not changed:
        return {"changed": {}, "keys": [], "nothing_changed": True, **_after(ctx)}
    return await write_properties(ctx, edited, changed, sorted(changed), user, title)


async def write_properties(
    ctx: ServerContext,
    edited: properties.PropertiesFile,
    changed: dict[str, str],
    others: list[str],
    user: str,
    title: str = "Changing game settings",
) -> dict[str, Any]:
    path = ctx.config.server_dir / properties.FILENAME

    async def change(job: JobHandle | None) -> dict[str, Any]:
        await asyncio.to_thread(edited.write, path)
        return {"changed": changed, "keys": others}

    async def check(result: dict[str, Any]) -> tuple[bool, str]:
        written = properties.PropertiesFile.read(path).values()
        expected = edited.values()
        wrong = [key for key, value in expected.items() if written.get(key) != value]
        if wrong:
            return False, f"{', '.join(wrong[:3])} didn't read back as saved"
        return True, f"{len(result['keys'])} setting(s) read back as saved"

    plan = SafeChange(
        title=title,
        change=change,
        check=check,
        stop_server=False,
        take_backup=path.is_file(),
        backup_name="pre-game-settings",
        backup_includes=[properties.FILENAME],
    )

    async def run(job: JobHandle | None) -> dict[str, Any]:
        return await run_safe_change(ctx.server, ctx.backups, plan, job=job, user=user)

    _, outcome = await ctx.core.jobs.run(
        "game_settings",
        f"{title} on {ctx.name}",
        run,
        server_id=ctx.server_id,
        risky=True,
        user=user,
    )
    _follow(ctx, edited.values())
    ctx.core.db.audit(
        "game_settings",
        user=user,
        target=ctx.server_id,
        detail=", ".join(others)[:300],
        server_id=ctx.server_id,
    )
    await ctx.bus.publish(
        Event(
            type="game_settings_changed",
            level="info",
            message=(
                f"Game settings changed on {ctx.name}"
                + (". They take effect after a restart." if ctx.server.running else ".")
            ),
            data={"changed": changed, "keys": others[:20], "running": ctx.server.running},
        )
    )
    return {
        "changed": changed,
        "keys": others,
        "undo": outcome.get("undo"),
        "safety_backup": (outcome.get("safety_backup") or {}).get("name"),
        **_after(ctx),
    }


def _after(ctx: ServerContext) -> dict[str, Any]:
    path = ctx.config.server_dir / properties.FILENAME
    modified = path.stat().st_mtime if path.is_file() else None
    return {
        "running": ctx.server.running,
        "restart_needed": waiting_for_restart(ctx, modified),
    }


def _follow(ctx: ServerContext, values: dict[str, str]) -> None:
    """Keep the app's own copies of the port and player limit in step with
    the file, which is what Minecraft actually uses."""
    updates: dict[str, Any] = {}
    port = (values.get("server-port") or "").strip()
    if port.isdigit() and int(port) != ctx.config.server.port:
        updates["server.port"] = int(port)
    players = (values.get("max-players") or "").strip()
    if players.isdigit() and int(players) != ctx.config.server.max_players:
        updates["server.max_players"] = int(players)
    if not updates:
        return
    for key, value in updates.items():
        ctx.config.set(key, value)
    try:
        ctx.config.save()
    except OSError:  # pragma: no cover - the file is still right; only the copy lags
        pass


# ---------------------------------------------------------------- vanilla spawns
# "Use vanilla spawn settings" (Technical, Paper and its forks): Spigot's
# entity activation range freezes mobs away from players and nerfs spawner
# mobs, which breaks many vanilla farms. A range of 0 keeps that kind of mob
# always active, as in vanilla. A fixed list: nothing else in the file
# changes, and Paper fills in every other setting itself when it starts.
SPIGOT_FILE = "spigot.yml"
_RANGES = "world-settings.default.entity-activation-range"
VANILLA_SPAWNS: tuple[tuple[str, Any], ...] = (
    *(
        (f"{_RANGES}.{kind}", 0)
        for kind in (
            "animals",
            "monsters",
            "raiders",
            "misc",
            "water",
            "villagers",
            "flying-monsters",
        )
    ),
    ("world-settings.default.nerf-spawner-mobs", False),
)


def write_yaml(path: Path, pairs: tuple[tuple[str, Any], ...]) -> None:
    """Set each dotted key in a YAML settings file, keeping its other settings."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not isinstance(data, dict):
        raise properties.FormError(
            {path.name: f"{path.name} couldn't be read, so it wasn't changed."}
        )
    for dotted, value in pairs:
        node = data
        *parents, last = dotted.split(".")
        for part in parents:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[last] = value
    temp = path.with_name(f".{path.name}.part")
    temp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.replace(temp, path)


def read_yaml(path: Path, dotted: str) -> Any:
    """One dotted key's value from a YAML settings file, or None."""
    try:
        node = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    for part in dotted.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def yaml_has(path: Path, pairs: tuple[tuple[str, Any], ...]) -> bool:
    return all(read_yaml(path, dotted) == value for dotted, value in pairs)


def write_vanilla_spawns(directory: Path) -> None:
    write_yaml(directory / SPIGOT_FILE, VANILLA_SPAWNS)


def read_vanilla_spawns(directory: Path) -> bool:
    """Whether spigot.yml holds every value of the fixed list now."""
    return yaml_has(directory / SPIGOT_FILE, VANILLA_SPAWNS)


async def use_vanilla_spawns(ctx: ServerContext, user: str = "system") -> dict[str, Any]:
    """Apply the fixed list as a safe change with one Undo. It takes effect
    the next time the server starts."""
    if not ctx.config.server_type.changes_farms:
        raise properties.FormError(
            {SPIGOT_FILE: f"{ctx.config.server_type.name} already spawns mobs like vanilla."}
        )
    result = await set_yaml(ctx, SPIGOT_FILE, VANILLA_SPAWNS, "Using vanilla spawn settings", user)
    ctx.core.db.audit("vanilla_spawns", user=user, target=ctx.server_id, server_id=ctx.server_id)
    return {**result, "changed": {SPIGOT_FILE: "vanilla spawns"}}


async def set_yaml(
    ctx: ServerContext,
    name: str,
    pairs: tuple[tuple[str, Any], ...],
    title: str,
    user: str = "system",
) -> dict[str, Any]:
    """Set keys in one YAML file in the server folder (spigot.yml,
    config/paper-global.yml) as a safe change with one Undo. It takes effect
    the next time the server starts."""
    path = ctx.config.server_dir / name

    async def change(job: JobHandle | None) -> dict[str, Any]:
        await asyncio.to_thread(write_yaml, path, pairs)
        return {"file": name}

    async def check(result: dict[str, Any]) -> tuple[bool, str]:
        ok = await asyncio.to_thread(yaml_has, path, pairs)
        return ok, f"{name} reads back as saved" if ok else f"{name} didn't read back"

    plan = SafeChange(
        title=title,
        change=change,
        check=check,
        stop_server=False,
        take_backup=path.is_file(),
        backup_name="pre-settings-file",
        backup_includes=[name],
        undo_scope=[name],
    )

    async def run(job: JobHandle | None) -> dict[str, Any]:
        return await run_safe_change(ctx.server, ctx.backups, plan, job=job, user=user)

    _, outcome = await ctx.core.jobs.run(
        "game_settings",
        f"{title} on {ctx.name}",
        run,
        server_id=ctx.server_id,
        risky=True,
        user=user,
    )
    return {
        "changed": {name: title},
        "keys": [dotted for dotted, _ in pairs],
        "undo": outcome.get("undo"),
        "safety_backup": (outcome.get("safety_backup") or {}).get("name"),
        **_after(ctx),
    }
