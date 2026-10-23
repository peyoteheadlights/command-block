"""The words on every screen, from the same [Simple, Technical] table as
the phone apps (shared/strings.json), so they never say different things."""

from __future__ import annotations

import json
from functools import cache

from .theme import SHARED

# Simple or Technical, for the whole window; the window sets it when the
# account's mode is read or switched, then rebuilds what shows words.
mode = {"technical": False}


@cache
def _table() -> dict[str, list[str]]:
    return json.loads((SHARED / "strings.json").read_text(encoding="utf-8"))


def text(key: str, technical: bool | None = None, **values) -> str:
    if technical is None:
        technical = mode["technical"]
    words = _table()[key][1 if technical else 0]
    return words.format(**values) if values else words


def has(key: str) -> bool:
    return key in _table()
