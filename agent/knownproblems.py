"""Known problems: what Diagnose server looks for, what it tells the owner,
and which change it offers.

Each problem says where to look (a join's failed line, a console line
during the run, or the newest crash), a short pattern, what to tell the
owner in plain words, an optional link (only shown, never opened by the
app) and, optionally, which change to offer. The change is only a name
from ``CHANGES``: the code in agent/diagnosefix.py makes it, after its own
checks, and still asks first. So data can add explanations and point at
changes that exist, but never adds a new kind of change or runs anything.

The built-in problems are below. New ones come with an app update (added
here) or between updates in the signed notes file's ``diagnose_problems``
(agent/notes.py), which fills ``FROM_NOTES``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import ServerContext

WHERE = ("join", "console", "crash")
# The changes a problem may point at. A new one needs code in diagnosefix.py.
CHANGES = ("", "restart", "eula", "memory", "whitelist", "unban")
MAX_PATTERN = 200
# Only the start of a long line is searched.
MAX_TEXT = 2000


@dataclass(frozen=True)
class Problem:
    id: str
    where: str
    pattern: re.Pattern[str]
    say: str
    link: str = ""
    change: str = ""


def _p(id: str, where: str, pattern: str, say: str, change: str = "") -> Problem:
    return Problem(id, where, re.compile(pattern, re.IGNORECASE), say, "", change)


BUILT_IN = [
    # Java "You are not white-listed on this server!", Bedrock "You are not
    # invited to this server" / disconnectionScreen.notAllowed.
    _p(
        "not-whitelisted",
        "join",
        r"white-?listed|not invited|notAllowed|allowlist",
        "The server turned the account away because it isn't on the whitelist.",
        "whitelist",
    ),
    _p(
        "banned",
        "join",
        r"\bbanned\b",
        "The server turned the account away because it is banned there.",
        "unban",
    ),
    _p(
        "no-answer",
        "join",
        r"^(Nothing answers on that port|No answer from that address)\.$",
        "Nothing answered on the server's game port from this PC.",
        "restart",
    ),
    _p(
        "out-of-memory",
        "crash",
        r"OutOfMemoryError",
        "The server ran out of memory.",
        "memory",
    ),
]
# Filled from the last good notes file.
FROM_NOTES: list[Problem] = []


def read(row: dict[str, Any]) -> Problem:
    """A problem from the notes file. ValueError says which field is bad;
    the id, say and link are checked by agent/notes.py first."""
    if row.get("where") not in WHERE:
        raise ValueError("where")
    if row.get("change", "") not in CHANGES:
        raise ValueError("change")
    text = row.get("pattern")
    if not isinstance(text, str) or not 0 < len(text) <= MAX_PATTERN:
        raise ValueError("pattern")
    try:
        pattern = re.compile(text, re.IGNORECASE)
    except re.error as exc:
        raise ValueError("pattern") from exc
    return Problem(
        row["id"], row["where"], pattern, row["say"], row.get("link", ""), row.get("change", "")
    )


def problems() -> list[Problem]:
    """The built-in ones, then the notes file's (a notes id that is already
    built in is left out)."""
    ids = {p.id for p in BUILT_IN}
    return BUILT_IN + [p for p in FROM_NOTES if p.id not in ids]


def matches(
    ctx: ServerContext, result: dict[str, Any], join_groups: set[str]
) -> list[tuple[Problem, dict[str, Any] | None]]:
    """The known problems this run saw: (problem, the failed join line) for a
    join problem, (problem, None) for a console or crash one. Each join
    problem once per join, the others once."""
    failed = [
        line
        for line in result["lines"]
        if line["ok"] is False and line["group"] in join_groups and line.get("detail")
    ]
    console = [
        line.raw[:MAX_TEXT]
        for line in ctx.server.console.since(result.get("console_seq", 0), limit=2000)
    ]
    crash = ""
    newest = ctx.crashes.list_crashes(1)
    if newest and newest[0]["ts"] >= result["started_at"]:
        row = newest[0]
        crash = "\n".join([str(row.get("category") or ""), str(row.get("summary") or "")])
        crash += "".join(f"\n{e}"[:MAX_TEXT] for e in row.get("evidence") or [])
    found: list[tuple[Problem, dict[str, Any] | None]] = []
    for problem in problems():
        search = problem.pattern.search
        if problem.where == "join":
            groups: set[str] = set()
            for line in failed:
                detail = line["detail"].removeprefix("The server said: ")[:MAX_TEXT]
                if line["group"] not in groups and search(detail):
                    groups.add(line["group"])
                    found.append((problem, line))
        elif (problem.where == "console" and any(search(x) for x in console)) or (
            problem.where == "crash" and crash and search(crash)
        ):
            found.append((problem, None))
    return found
