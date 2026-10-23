"""Lock-screen alerts for the iPhone app, through Apple's push service (APNs).

The same idea as fcm.py, sent straight to Apple: no relay and nobody else's
server. A message carries no words about the PC, only "New alert" and the
alert's number; the app's notification extension reads the alert itself
from ``/api/alerts`` over Tailscale and shows its words instead. So Apple
sees which iPhone is woken and when, never a server's name, a player or a
message.

Setting it up needs the owner's own Apple key (a ``.p8`` file from the
Apple Developer site, see docs/notifications.md), its Key ID and the team
ID. They live only in the data folder, in a folder only this account can
read (``python -m installer.setup_phone_alerts --apple <file> --team <id>``
puts them there). Without them nothing here sends anything, and the app
still catches up when it's opened.

iPhones are rows in ``app_phones`` with platform "ios", registered and
forgotten exactly like Android phones (fcm.py). Apple answering that an
address is gone deletes the row.

Apple only talks HTTP/2, which is why httpx has its ``http2`` extra. Only
the two addresses below are ever contacted, and no test touches the network
(``TRANSPORT`` is swapped for a mock).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from .fcm import SendResult

log = logging.getLogger("msc.apns")

# Swapped for an httpx.MockTransport in the tests.
TRANSPORT: httpx.AsyncBaseTransport | None = None

SERVER = "https://api.push.apple.com"
# A development build installed from Xcode gets its addresses from here.
SANDBOX = "https://api.sandbox.push.apple.com"
TOPIC = "io.github.peyoteheadlights.commandblock"  # the iPhone app's bundle ID
KEY_FILE = "phone-alerts/apns-key.p8"
SETTINGS_FILE = "phone-alerts/apns.json"
TIMEOUT = 15.0
# Apple wants a new sign-in token at most every 20 minutes and refuses one
# older than an hour.
TOKEN_FOR = 50 * 60
# How long Apple keeps trying an iPhone that is off. The app catches up anyway.
KEEP_FOR = 3600
TEN = re.compile(r"^[A-Z0-9]{10}$")  # Apple's Key IDs and team IDs


class ApnsError(Exception):
    pass


@dataclass(frozen=True)
class AppleKey:
    key_id: str
    team_id: str
    sandbox: bool
    private_key: ec.EllipticCurvePrivateKey


def key_path(config) -> Path:
    return config.resolve_data(KEY_FILE)


def settings_path(config) -> Path:
    return config.resolve_data(SETTINGS_FILE)


def read_key(data: bytes) -> ec.EllipticCurvePrivateKey:
    """The private key from a .p8 file. Raises ApnsError with a plain
    reason; the key itself never appears in a message or a log."""
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except ValueError as exc:
        raise ApnsError("That file isn't an Apple push key (.p8).") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ApnsError("That file isn't an Apple push key (.p8).")
    return key


def check_ids(key_id: str, team_id: str) -> None:
    if not TEN.match(key_id):
        raise ApnsError("The Key ID is 10 letters and numbers, like ABC123DEFG.")
    if not TEN.match(team_id):
        raise ApnsError("The team ID is 10 letters and numbers, like ABC123DEFG.")


def load_key(config) -> AppleKey:
    try:
        settings = json.loads(settings_path(config).read_text(encoding="utf-8"))
        private_key = read_key(key_path(config).read_bytes())
    except FileNotFoundError as exc:
        raise ApnsError("iPhone alerts through Apple aren't set up on this PC.") from exc
    except (OSError, ValueError) as exc:
        raise ApnsError("The Apple key's settings can't be read.") from exc
    if not isinstance(settings, dict):
        raise ApnsError("The Apple key's settings can't be read.")
    key_id, team_id = str(settings.get("key_id", "")), str(settings.get("team_id", ""))
    check_ids(key_id, team_id)
    return AppleKey(key_id, team_id, settings.get("sandbox") is True, private_key)


def configured(config) -> bool:
    try:
        load_key(config)
    except ApnsError:
        return False
    return True


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_in_token(key: AppleKey, now: float | None = None) -> str:
    """Apple's provider token: a JWT signed with the key (ES256)."""
    header = {"alg": "ES256", "kid": key.key_id}
    claims = {"iss": key.team_id, "iat": int(now if now is not None else time.time())}
    signing_input = (
        _b64(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64(json.dumps(claims, separators=(",", ":")).encode())
    )
    r, s = decode_dss_signature(
        key.private_key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    )
    return signing_input + "." + _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


class Sender:
    """Sends wake-ups, reusing the sign-in token for 50 minutes."""

    def __init__(self, config):
        self.config = config
        self._token: str | None = None
        self._token_at = 0.0

    def _sign_in(self, key: AppleKey) -> str:
        if not self._token or time.time() - self._token_at > TOKEN_FOR:
            self._token, self._token_at = sign_in_token(key), time.time()
        return self._token

    async def wake(self, phone_tokens: list[str], alert_id: int) -> dict[str, SendResult]:
        """Wake each iPhone about alert ``alert_id``. Never raises."""
        results: dict[str, SendResult] = {}
        if not phone_tokens:
            return results
        try:
            key = load_key(self.config)
        except ApnsError as exc:
            return {t: SendResult(False, detail=str(exc)) for t in phone_tokens}
        server = SANDBOX if key.sandbox else SERVER
        try:
            async with httpx.AsyncClient(
                http2=True, timeout=TIMEOUT, transport=TRANSPORT
            ) as client:
                for phone in phone_tokens:
                    results[phone] = await self._send_one(client, server, key, phone, alert_id)
        except httpx.HTTPError:
            for phone in phone_tokens:
                results.setdefault(
                    phone, SendResult(False, detail="Apple's push service didn't answer.")
                )
        return results

    async def _send_one(
        self, client: httpx.AsyncClient, server: str, key: AppleKey, phone: str, alert_id: int
    ) -> SendResult:
        message = {
            # The words are generic; the app's extension swaps them for the
            # alert it reads from the PC.
            "aps": {"alert": {"body": "New alert"}, "mutable-content": 1, "sound": "default"},
            "kind": "alert",
            "alert": str(int(alert_id)),
        }
        response = await client.post(
            f"{server}/3/device/{phone}",
            json=message,
            headers={
                "authorization": f"bearer {self._sign_in(key)}",
                "apns-topic": TOPIC,
                "apns-push-type": "alert",
                "apns-priority": "10",
                "apns-expiration": str(int(time.time()) + KEEP_FOR),
            },
        )
        if response.status_code == 200:
            return SendResult(True)
        reason = _reason(response)
        if response.status_code == 410:
            return SendResult(
                False, gone=True, detail="This iPhone's push address is no longer valid."
            )
        if reason == "BadDeviceToken":
            # Kept: it's often a development build with the key set up
            # without --sandbox (or the other way round), not a gone phone.
            return SendResult(
                False, detail="Apple doesn't know this iPhone's address (a development build?)."
            )
        if reason in ("ExpiredProviderToken", "InvalidProviderToken"):
            self._token = None  # signed again next time
        return SendResult(
            False,
            detail=f"Apple's push service answered HTTP {response.status_code}"
            + (f" ({reason})." if reason else "."),
        )


def _reason(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    return str(body.get("reason", "")) if isinstance(body, dict) else ""
