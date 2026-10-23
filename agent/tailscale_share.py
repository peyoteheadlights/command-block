"""Tailscale share links for moderator invites, made by the agent (Technical).

A moderator reaches this PC through a Tailscale share. Without a key the owner
opens Tailscale's Share page and pastes the link into the invite
(desktop/moderators.py). With a key saved here, "Make a share link" asks
Tailscale's API for a one-time share invite for this PC instead
(POST /api/v2/device/{id}/device-invites; unused links run out after 30 days).

Two kinds of key:

- an OAuth client (id and secret), kept until it is revoked. Give it only the
  device-invites scope and read access to devices;
- an API access token, which has full rights and runs out after 1 to 90
  days. The agent alerts a week before it does.

The key is kept in the data folder like the Firebase key, never sent back,
and "Remove key" deletes it; revoking it is done on Tailscale's admin page.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .security.certs import secure_directory

API = "https://api.tailscale.com/api/v2"
KEY_FILE = "secrets/tailscale-share-key.json"
TRANSPORT: httpx.AsyncBaseTransport | None = None  # swapped for a mock in tests
TIMEOUT = 15.0
REVOKE_PAGES = {
    "oauth": "https://login.tailscale.com/admin/settings/oauth",
    "token": "https://login.tailscale.com/admin/settings/keys",
}
WARN_DAYS = 7


class ShareError(Exception):
    """Said to the owner as it is. Never carries the key."""


def key_path(config) -> Path:
    return config.resolve_data(KEY_FILE)


def load(config) -> dict[str, Any] | None:
    try:
        return json.loads(key_path(config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def state(config) -> dict[str, Any]:
    saved = load(config) or {}
    expires = saved.get("expires_at")
    return {
        "saved": bool(saved),
        "kind": saved.get("kind"),
        "expires_at": expires,
        "expiring": bool(expires and expires - time.time() < WARN_DAYS * 86400),
    }


def _check(kind: str, secret: str, client_id: str | None) -> None:
    if kind == "token" and not secret.startswith("tskey-api-"):
        raise ShareError("That isn't an API access token (it starts with tskey-api-).")
    if kind == "oauth" and not (client_id and secret.startswith("tskey-client-")):
        raise ShareError(
            "An OAuth client needs its client ID and its secret (it starts with tskey-client-)."
        )


async def _bearer(client: httpx.AsyncClient, key: dict[str, Any]) -> str:
    if key["kind"] == "token":
        return key["secret"]
    answer = await client.post(
        f"{API}/oauth/token",
        data={
            "client_id": key["client_id"],
            "client_secret": key["secret"],
            "grant_type": "client_credentials",
        },
    )
    if answer.status_code != 200:
        raise ShareError(f"Tailscale didn't accept the OAuth client (error {answer.status_code}).")
    return str(answer.json().get("access_token") or "")


async def _device(client: httpx.AsyncClient, bearer: str, address: str) -> str:
    """This PC's device id in Tailscale's API, found by its Tailscale address."""
    answer = await client.get(
        f"{API}/tailnet/-/devices", headers={"Authorization": f"Bearer {bearer}"}
    )
    if answer.status_code != 200:
        raise ShareError(f"Tailscale refused the key (error {answer.status_code}).")
    for device in answer.json().get("devices") or []:
        if address in (device.get("addresses") or []):
            return str(device.get("nodeId") or device.get("id"))
    raise ShareError("Tailscale's list of devices doesn't have this PC.")


async def _token_expiry(client: httpx.AsyncClient, token: str) -> float | None:
    """When an API access token runs out, as Tailscale says. None: not said."""
    parts = token.split("-")  # tskey-api-<key id>-<secret>
    if len(parts) < 4:
        return None
    answer = await client.get(
        f"{API}/tailnet/-/keys/{parts[2]}", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        expires = str(answer.json().get("expires")) if answer.status_code == 200 else ""
        return dt.datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


async def save(
    config, address: str, kind: str, secret: str, client_id: str | None = None
) -> dict[str, Any]:
    """Check the key with Tailscale (it must find this PC), then keep it."""
    secret, client_id = secret.strip(), (client_id or "").strip() or None
    _check(kind, secret, client_id)
    key: dict[str, Any] = {"kind": kind, "secret": secret, "client_id": client_id}
    async with httpx.AsyncClient(timeout=TIMEOUT, transport=TRANSPORT) as client:
        try:
            await _device(client, await _bearer(client, key), address)
            if kind == "token":
                key["expires_at"] = await _token_expiry(client, secret)
        except httpx.HTTPError as exc:
            raise ShareError(f"Tailscale couldn't be reached: {type(exc).__name__}") from exc
    target = key_path(config)
    secure_directory(target.parent)
    temporary = target.with_suffix(".part")
    temporary.write_text(json.dumps(key), encoding="utf-8")
    os.replace(temporary, target)
    return state(config)


def remove(config) -> dict[str, Any]:
    kind = (load(config) or {}).get("kind")
    key_path(config).unlink(missing_ok=True)
    return {**state(config), "revoke_page": REVOKE_PAGES.get(kind or "")}


async def make_link(config, address: str) -> str:
    """A one-time share link for this PC."""
    key = load(config)
    if not key:
        raise ShareError("Save a Tailscale key first.")
    async with httpx.AsyncClient(timeout=TIMEOUT, transport=TRANSPORT) as client:
        try:
            bearer = await _bearer(client, key)
            device = await _device(client, bearer, address)
            answer = await client.post(
                f"{API}/device/{device}/device-invites",
                headers={"Authorization": f"Bearer {bearer}"},
                json=[{"multiUse": False, "allowExitNode": False}],
            )
        except httpx.HTTPError as exc:
            raise ShareError(f"Tailscale couldn't be reached: {type(exc).__name__}") from exc
    if answer.status_code != 200:
        raise ShareError(f"Tailscale didn't make the link (error {answer.status_code}).")
    try:
        url = str(answer.json()[0]["inviteUrl"])
    except (ValueError, LookupError, TypeError) as exc:
        raise ShareError("Tailscale's answer had no share link in it.") from exc
    if not url.startswith("https://"):
        raise ShareError("Tailscale's answer had no share link in it.")
    return url
