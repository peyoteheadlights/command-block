"""The game rules screen: every rule and its value, read from the running
server, never from a list per Minecraft version.

Java: ``help gamerule`` lists the rules this server has, then
``gamerule <name>`` asks each one's value. Bedrock: ``gamerule`` answers
with all of them on one line. Rules change live, so nothing waits for a
restart. A preset is a few rules at once; a rule this server doesn't list
(an older or newer Minecraft names it differently) is skipped and said so.
"""

from __future__ import annotations

import re

from .minecraft.ask import ask
from .minecraft.commands import validate

JAVA_RULE_RE = re.compile(r"^/gamerule\s+(\S+)")
# "Gamerule keepInventory is currently set to: false" (newer versions say "Game rule").
JAVA_VALUE_RE = re.compile(r"^Game ?rule (\S+) is currently set to: (\S+)$", re.IGNORECASE)
BEDROCK_VALUE_RE = re.compile(r"([a-z_]+) = ([^,\s]+)", re.IGNORECASE)
MAX_RULES = 200

# Draft names, keys presets.* in shared/strings.json. Rules are matched by
# their plain name (see plain()), so keepInventory, keep_inventory and
# Bedrock's keepinventory are the same rule.
PRESETS: dict[str, dict[str, bool]] = {
    "friendly": {
        "keepinventory": True,
        "mobgriefing": False,
        "doinsomnia": False,
        "dofiretick": False,
    },
    "builders": {
        "dodaylightcycle": False,
        "doweathercycle": False,
        "domobspawning": False,
        "keepinventory": True,
    },
    "survival": {
        "keepinventory": False,
        "mobgriefing": True,
        "doinsomnia": True,
        "dofiretick": True,
        "dodaylightcycle": True,
        "doweathercycle": True,
        "domobspawning": True,
    },
}


class GameRuleError(ValueError):
    pass


def plain(name: str) -> str:
    return name.lower().removeprefix("minecraft:").replace("_", "")


def value_of(said: str) -> bool | int | str:
    if said.lower() in ("true", "false"):
        return said.lower() == "true"
    try:
        return int(said)
    except ValueError:
        return said  # shown as the server said it


def _running(ctx) -> None:
    if ctx.server.state.value != "ONLINE":
        raise GameRuleError("Start the server first: game rules are read from the running server.")


async def read(ctx) -> list[dict]:
    """[{"name", "value"}], in the server's own order. Empty: it didn't answer."""
    _running(ctx)
    server = ctx.server
    if ctx.config.server_type.edition == "bedrock":
        lines = await ask(server, ["gamerule"], lambda s: " = " in s)
        pairs = [m.groups() for line in lines for m in BEDROCK_VALUE_RE.finditer(line)]
    else:
        lines = await ask(server, ["help gamerule"], lambda s: bool(JAVA_RULE_RE.match(s)))
        names = list(dict.fromkeys(JAVA_RULE_RE.match(s).group(1) for s in lines))  # type: ignore[union-attr]
        names = names[:MAX_RULES]
        answers = (
            await ask(
                server, [f"gamerule {n}" for n in names], lambda s: bool(JAVA_VALUE_RE.match(s))
            )
            if names
            else []
        )
        said: dict[str, str] = {}
        for answer in answers:
            name, value = JAVA_VALUE_RE.match(answer).groups()  # type: ignore[union-attr]
            said[name] = value
        pairs = [(n, said[n]) for n in names if n in said]
    return [{"name": name, "value": value_of(value)} for name, value in pairs[:MAX_RULES]]


def _text(value: bool | int) -> str:
    return str(value).lower() if isinstance(value, bool) else str(int(value))


async def change(ctx, wanted: dict[str, bool | int]) -> dict:
    """Set rules the server listed, then read them back. Answers the rules
    as the server now says, what each changed one was before (for Undo) and
    the ones skipped because this server doesn't have them."""
    rules = await read(ctx)
    if not rules:
        raise GameRuleError(
            "The server didn't say which game rules it has, so nothing was changed."
        )
    by_plain = {plain(r["name"]): r for r in rules}
    before: dict[str, bool | int | str] = {}
    skipped: list[str] = []
    for name, value in wanted.items():
        rule = by_plain.get(plain(name))
        if rule is None:
            skipped.append(name)
            continue
        if isinstance(rule["value"], bool) != isinstance(value, bool):
            kind = "true or false" if isinstance(rule["value"], bool) else "a whole number"
            raise GameRuleError(f"{rule['name']} takes {kind}.")
        if rule["value"] != value:
            before[rule["name"]] = rule["value"]
    for name in before:
        value = next(v for k, v in wanted.items() if plain(k) == plain(name))
        command = validate(f"gamerule {name} {_text(value)}", confirm=True)
        await ctx.server.send_command(command.raw)
    rules = await read(ctx) if before else rules
    return {"rules": rules, "before": before, "skipped": skipped}
