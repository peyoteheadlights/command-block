"""Asking a Bedrock server whether it is there: NetherNet's join page.

From Bedrock 1.26.50 the dedicated server talks NetherNet by default
(server.properties ``transport=nethernet``), and from 26.60 RakNet is gone.
A NetherNet server answers nothing on UDP 19132. Instead its game port is a
TCP port with a small web page, ``GET /v1/join``, that sends the server's
name, version and player count as JSON. The same check mc-monitor uses.
"""

from __future__ import annotations

import http.client
import json
import urllib.request
from typing import Any

# No proxy: the server is asked directly, even on a PC with a proxy set.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def parse_join(text: str) -> dict[str, Any] | None:
    """The join page's fields, or None when ``text`` isn't one."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("version"), str):
        return None

    def number(key: str) -> int | None:
        value = data.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return {
        "status": text,
        # Not sent by NetherNet; left empty rather than guessed.
        "edition": None,
        "name": data.get("name") if isinstance(data.get("name"), str) else None,
        "version": data["version"],
        "players": number("players"),
        "max_players": number("maxPlayers"),
    }


def ping(host: str, port: int, timeout: float = 1.5) -> dict[str, Any] | None:
    """Ask the join page, over http then https. None: no answer."""
    where = f"[{host}]" if ":" in host else host
    for scheme in ("http", "https"):
        try:
            with _OPENER.open(f"{scheme}://{where}:{int(port)}/v1/join", timeout=timeout) as reply:
                text = reply.read(1 << 16).decode("utf-8", errors="replace")
        except (OSError, ValueError, http.client.HTTPException):
            continue
        answer = parse_join(text)
        if answer is not None:
            return answer
    return None
