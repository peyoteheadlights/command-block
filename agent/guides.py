"""Add-on guides: what an installed add-on does, and its common commands as
buttons.

Each guide is one JSON file in ``data/guides/`` (adding a guide is adding a
file; ``tests/test_guides.py`` checks every one). A guide names the add-on
by the name inside its jar and the add-on versions it was written for. It is
shown only when that add-on is installed and its version is in the range, so
nobody is shown a command their version doesn't have.

A button is a command template with typed fields: a number with a range, a
player name, on/off, a choice from a list, a world of this server or a short
name. The template is filled only from those checked values, then goes
through the same console validation every command passes
(agent/minecraft/commands.py). A button that changes the world, or whose
command is one of the "needs confirmation" ones, asks first. What the
console says back in the next few seconds is returned as it is.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .minecraft.commands import describe_danger, validate
from .minecraft.playeractions import check_name
from .mods.jarinfo import range_satisfies
from .security import permissions

if TYPE_CHECKING:
    from .core import ServerContext

FOLDER = Path(__file__).resolve().parent.parent / "data" / "guides"
SCHEMA = 1
KINDS = ("number", "player", "toggle", "choice", "world", "name")
# What a button may need: sending to the console, or a player button's right.
BUTTON_PERMISSIONS = (
    permissions.CONSOLE_SEND,
    permissions.PLAYERS_MANAGE,
    permissions.PLAYERS_OP,
)
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
FIELD_RE = re.compile(r"\{([a-z_]+)\}")
ANSWER_WAIT = 3.0  # seconds to wait for the console's first answer
QUIET = 0.5  # and then for it to stop talking


class GuideError(ValueError):
    pass


@dataclass(frozen=True)
class Guide:
    id: str
    data: dict[str, Any]

    @property
    def names(self) -> set[str]:
        return {n.lower() for n in self.data["addon"]["names"]}

    def button(self, button_id: str) -> dict[str, Any]:
        for button in self.data["buttons"]:
            if button["id"] == button_id:
                return button
        raise GuideError("That guide has no such button.")


def check_guide(data: Any, name: str = "") -> Guide:
    """A guide file read and checked, or GuideError saying what's wrong."""

    def need(ok: object, what: str) -> None:
        if not ok:
            raise GuideError(f"{name or 'guide'}: {what}")

    need(isinstance(data, dict) and data.get("schema") == SCHEMA, f"schema has to be {SCHEMA}")
    for key in ("id", "title", "what", "versions", "source", "checked"):
        need(isinstance(data.get(key), str) and data[key].strip(), f"{key} is missing")
    need(not name or data["id"] == name, "id has to match the file name")
    addon = data.get("addon")
    need(
        isinstance(addon, dict)
        and addon.get("content") in ("plugins", "mods")
        and isinstance(addon.get("names"), list)
        and addon["names"]
        and all(isinstance(n, str) and n for n in addon["names"]),
        "addon needs content and names",
    )
    need(
        all(range_satisfies("1.0.0", part) is not None for part in data["versions"].split()),
        "versions isn't a range",
    )
    need(
        isinstance(data.get("steps"), list)
        and data["steps"]
        and all(isinstance(s, str) and s for s in data["steps"]),
        "steps are missing",
    )
    buttons = data.get("buttons")
    need(isinstance(buttons, list), "buttons has to be a list")
    seen: set[str] = set()
    for button in buttons:
        need(isinstance(button, dict), "a button isn't an object")
        bid = button.get("id")
        need(isinstance(bid, str) and bid and bid not in seen, "a button id is missing or twice")
        seen.add(bid)
        where = f"button {bid}"
        need(isinstance(button.get("label"), str) and button["label"], f"{where} has no label")
        need(
            button.get("permission", permissions.CONSOLE_SEND) in BUTTON_PERMISSIONS,
            f"{where} needs a permission a button can use",
        )
        need(isinstance(button.get("changes_world", False), bool), f"{where} changes_world")
        template = button.get("command")
        need(isinstance(template, str) and template, f"{where} has no command")
        fields = button.get("fields") or []
        ids = [f.get("id") for f in fields if isinstance(f, dict)]
        need(len(ids) == len(fields) == len(set(ids)), f"{where} has a broken field list")
        need(set(FIELD_RE.findall(template)) == set(ids), f"{where} fields and command differ")
        for field in fields:
            _check_field(field, f"{where} field {field['id']}", need)
        # The template with every field at its sample value has to pass the
        # console checks, and a "needs confirmation" command has to ask.
        try:
            filled = template.format(**{f["id"]: _sample(f) for f in fields})
            validate(filled, confirm=True)
        except (KeyError, IndexError, ValueError) as exc:
            raise GuideError(f"{name}: {where}: {exc}") from exc
        need(
            not describe_danger(filled) or button.get("changes_world") is True,
            f"{where} sends a command that needs confirming, so changes_world has to be true",
        )
    return Guide(data["id"], data)


def _check_field(field: dict[str, Any], where: str, need) -> None:
    kind = field.get("kind")
    need(kind in KINDS, f"{where} has an unknown kind")
    need(isinstance(field.get("label"), str) and field["label"], f"{where} has no label")
    if kind == "number":
        low, high = field.get("min"), field.get("max")
        default = field.get("default", low)
        need(
            isinstance(low, int)
            and isinstance(high, int)
            and isinstance(default, int)
            and low <= default <= high,
            f"{where} needs min <= default <= max",
        )
    if kind == "toggle":
        need(
            isinstance(field.get("on"), str) and isinstance(field.get("off"), str),
            f"{where} needs on and off",
        )
    if kind == "choice":
        options = field.get("options")
        need(
            isinstance(options, list)
            and options
            and all(
                isinstance(o, dict) and NAME_RE.match(str(o.get("value", ""))) and o.get("label")
                for o in options
            ),
            f"{where} needs options with a value and a label",
        )


def _sample(field: dict[str, Any]) -> str:
    """A value the field accepts, to check the command it builds."""
    kind = field["kind"]
    if kind == "number":
        return str(field.get("default", field["min"]))
    if kind == "toggle":
        return field["on"]
    if kind == "choice":
        return field["options"][0]["value"]
    if kind == "world":
        return "world"
    return "Alex"


def in_range(version: str, spec: str) -> bool:
    """Every part of ``spec`` (">=1.3.0 <2") holds for ``version``. A version
    that can't be compared is out: no guide is better than a wrong one."""
    return all(range_satisfies(version, part) is True for part in spec.split())


def load(folder: Path = FOLDER) -> dict[str, Guide]:
    """Every guide that ships with the app, by id. A broken file is skipped
    here (the test catches it before a release)."""
    found: dict[str, Guide] = {}
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            guide = check_guide(json.loads(path.read_text("utf-8")), path.stem)
        except (OSError, ValueError):
            continue
        found[guide.id] = guide
    return found


_loaded: dict[str, Guide] | None = None


def shipped() -> dict[str, Guide]:
    global _loaded
    if _loaded is None:
        _loaded = load()
    return _loaded


# ------------------------------------------------------------ for one server
def worlds(ctx: ServerContext) -> list[str]:
    """This server's worlds by folder name: the ones that exist, or the
    level-name before the first start."""
    from .minecraft import properties

    directory = ctx.config.server_dir
    names = properties.world_folders(directory, ctx.config.server_type)
    there = sorted(n for n in names if (directory / n / "level.dat").is_file())
    if there:
        return there
    path = directory / properties.FILENAME
    values = properties.PropertiesFile.read(path).values() if path.is_file() else {}
    return [properties.world_folder(directory, values).name]


def for_server(ctx: ServerContext, held: frozenset[str]) -> list[dict[str, Any]]:
    """The guides for this server's installed add-ons, with only the buttons
    ``held`` permissions allow."""
    content = ctx.config.server_type.content
    if not ctx.config.server_type.has_content:
        return []
    installed = ctx.mods.scan(use_cache=True)
    shown: list[dict[str, Any]] = []
    world_names = None
    for guide in shipped().values():
        if guide.data["addon"]["content"] != content:
            continue
        mod = next((m for m in installed if m.enabled and m.mod_id.lower() in guide.names), None)
        if mod is None or not in_range(mod.version, guide.data["versions"]):
            continue
        buttons = []
        for button in guide.data["buttons"]:
            if button.get("permission", permissions.CONSOLE_SEND) not in held:
                continue
            fields = []
            for field in button.get("fields") or []:
                if field["kind"] == "world":
                    world_names = world_names or worlds(ctx)
                    field = {**field, "options": [{"value": w, "label": w} for w in world_names]}
                fields.append(field)
            buttons.append(
                {
                    "id": button["id"],
                    "label": button["label"],
                    "help": button.get("help", ""),
                    "command": button["command"],
                    "fields": fields,
                    "changes_world": bool(button.get("changes_world")),
                    "warning": button.get("warning") or describe_danger(button["command"]),
                }
            )
        shown.append(
            {
                "id": guide.id,
                "title": guide.data["title"],
                "what": guide.data["what"],
                "steps": guide.data["steps"],
                "source": guide.data["source"],
                "filename": mod.filename,
                "addon_version": mod.version,
                "buttons": buttons,
            }
        )
    return shown


def fill(ctx: ServerContext, button: dict[str, Any], values: dict[str, Any]) -> str:
    """The command with each field's checked value. Anything the field
    doesn't accept is refused, never trimmed or guessed into shape."""
    filled: dict[str, str] = {}
    for field in button.get("fields") or []:
        label, raw = field["label"], values.get(field["id"])
        kind = field["kind"]
        if kind == "number":
            if isinstance(raw, bool) or not isinstance(raw, (int, str)):
                raise GuideError(f"{label}: enter a whole number.")
            try:
                number = int(str(raw).strip())
            except ValueError:
                raise GuideError(f"{label}: enter a whole number.") from None
            if not field["min"] <= number <= field["max"]:
                raise GuideError(f"{label}: enter {field['min']} to {field['max']}.")
            filled[field["id"]] = str(number)
        elif kind == "toggle":
            if not isinstance(raw, bool):
                raise GuideError(f"{label}: pick on or off.")
            filled[field["id"]] = field["on"] if raw else field["off"]
        elif kind in ("choice", "world"):
            options = worlds(ctx) if kind == "world" else [o["value"] for o in field["options"]]
            if raw not in options:
                raise GuideError(f"{label}: pick one from the list.")
            filled[field["id"]] = str(raw)
        elif kind == "player":
            try:
                filled[field["id"]] = check_name(str(raw or ""))
            except ValueError as exc:
                raise GuideError(f"{label}: {exc}") from None
        else:  # name
            if not isinstance(raw, str) or not NAME_RE.match(raw.strip()):
                raise GuideError(f"{label}: use up to 32 letters, numbers, _ or -.")
            filled[field["id"]] = raw.strip()
    return button["command"].format(**filled)


def find(ctx: ServerContext, guide_id: str, held: frozenset[str]) -> Guide:
    """A guide shown on this server now, or GuideError."""
    if not any(g["id"] == guide_id for g in for_server(ctx, held)):
        raise GuideError("That guide isn't for an add-on on this server.")
    return shipped()[guide_id]


async def press(
    ctx: ServerContext,
    guide_id: str,
    button_id: str,
    values: dict[str, Any],
    confirm: bool,
    held: frozenset[str],
) -> dict[str, Any]:
    """Send one button's command and return what the console said."""
    from .minecraft.process import ServerError
    from .security.auth import AuthError

    guide = find(ctx, guide_id, held)
    button = guide.button(button_id)
    if button.get("permission", permissions.CONSOLE_SEND) not in held:
        raise AuthError("Your account can't use this button.", status=403)
    command = fill(ctx, button, values)
    asks = bool(button.get("changes_world")) or bool(describe_danger(command))
    if asks and not confirm:
        raise GuideError(
            "This changes the world, so it has to be confirmed first. "
            + (button.get("warning") or describe_danger(command) or "")
        )
    validated = validate(command, confirm=True)
    if not ctx.server.running or ctx.server.state.value != "ONLINE":
        raise ServerError("The server has to be running for this. Start it, then try again.")
    console = ctx.server.console
    last = console.tail(1)
    after = last[0].seq if last else 0
    await ctx.server.send_command(validated.raw)
    lines = await answer(console, after)
    return {
        "command": validated.raw,
        "lines": [{"raw": line.raw, "level": line.level} for line in lines],
        "answered": bool(lines),
    }


async def answer(console, after: int) -> list:
    """The console's lines after ``after`` (not the command itself), once it
    has said something and gone quiet, or after ANSWER_WAIT seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ANSWER_WAIT
    heard_at = None
    count = 0
    while loop.time() < deadline:
        lines = [ln for ln in console.since(after) if ln.source != "command"]
        if len(lines) != count:
            count, heard_at = len(lines), loop.time()
        elif heard_at is not None and loop.time() - heard_at >= QUIET:
            break
        await asyncio.sleep(0.05)
    return [ln for ln in console.since(after) if ln.source != "command"][:40]
