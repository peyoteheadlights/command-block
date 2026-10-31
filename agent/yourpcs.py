"""Your PCs: one owner account on several PCs, with no cloud and no main PC.

Each PC keeps a full copy of the signed account records
(agent/security/records.py) and sends what changed straight to the others
over their normal HTTPS port, pinned to the certificate each PC saw when it
joined, and with this PC's own sync key. Like each Java client holding its
own copy of a list and pushing edits to the others, with "newest wins".

* A change here is sent at once to every PC; a PC that can't be reached is
  tried again later (5 s, 30 s, 2 min, then every 10 min) until it answers.
  Nothing runs while nothing changed.
* At start this PC also asks every other PC what changed while it was off.
* Joining: PC A makes a join link (its pairing link plus a code that works
  once, for 10 minutes). The new PC sends that code, the owner's name and
  password and its own address, certificate and sync key. A then waits for
  an owner device that was signed in before the request to press Allow.
  After that the new PC gets the signing key and a full copy.
* Remove: the PC's record gets a "removed" marker, so its sync key stops
  working on every PC that hears of it. The removed PC, when it hears,
  goes back to its own separate owner account.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import platform
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from . import pairing
from .events import Event
from .security.auth import AuthError, verify_password
from .security.tls import pinned_context

log = logging.getLogger("msc.yourpcs")

LINK_SECONDS = 10 * 60
RETRY = (5, 30, 120, 600)
HELLO_EVERY = 3
PC_HEADER = "X-Command-Block-PC"
KEY_HEADER = "X-Command-Block-Sync-Key"


class PcError(ValueError):
    """Said to the owner as it is."""


class Refused(Exception):
    """A sync call this PC refuses: ``status`` 403, or 410 for a removed PC."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def read_join_link(link: str) -> dict[str, Any]:
    parsed = urlparse((link or "").strip())
    query = parse_qs(parsed.query)
    fingerprint = (query.get("fp") or [""])[0].lower()
    code = (query.get("join") or [""])[0]
    try:
        port = parsed.port
    except ValueError:
        port = None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not port
        or len(fingerprint) != 64
        or not code
    ):
        raise PcError(
            "That isn't a join link. On your other PC open Settings > Your PCs > Add a PC "
            "and copy the link it shows."
        )
    return {"host": parsed.hostname, "port": port, "fingerprint": fingerprint, "code": code}


class YourPCs:
    def __init__(self, core) -> None:
        self.core = core
        self.db = core.db
        self.auth = core.auth
        self.records = core.auth.records
        self.records.applied["pc"] = self._apply_pc
        self.records.changed.append(self._soon)
        # Per other PC: how far it has our changes (pushed) and we its
        # (pulled), when it last answered, since when it doesn't.
        self.state: dict[str, dict[str, Any]] = self.db.get_setting("pcs_state", {}) or {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    # -- facts ---------------------------------------------------------------
    @property
    def pc(self) -> str:
        return self.records.pc

    def peers(self) -> list[dict[str, Any]]:
        return [r for r in self.records.all("pc") if r["key"] != self.pc]

    def joined(self) -> bool:
        return self.auth.joined()

    @staticmethod
    def name() -> str:
        return platform.node() or "This PC"

    def label(self) -> str | None:
        """This PC's name for alerts, once it shares its accounts."""
        return self.name() if self.joined() else None

    def _me(self) -> dict[str, Any]:
        code = pairing.pairing_code(self.core.config)
        return {
            "name": self.name(),
            "address": code["address"],
            "port": code["port"],
            "fingerprint": code["fingerprint"],
            "sync_key_hash": self.records.sync_key_hash(),
        }

    def _record_me(self) -> None:
        """Keep this PC's own record right (a new certificate, a new port)."""
        old = self.records.get("pc", self.pc)
        me = self._me()
        if not me["address"] or not me["fingerprint"]:
            return
        if old is None or old["body"] != me:
            self.records.put("pc", self.pc, me)

    def _peer_state(self, pc: str) -> dict[str, Any]:
        return self.state.setdefault(pc, {"pushed": 0, "pulled": 0, "tries": 0, "next": 0})

    def _save_state(self) -> None:
        self.db.set_setting("pcs_state", self.state)

    def reach(self, pc: str) -> dict[str, Any]:
        """What the apps say about one PC. "ok" only when it answered and has
        every change; never a guess."""
        state = self.state.get(pc) or {}
        behind = self.records.last_seq() > int(state.get("pushed") or 0) and any(
            row["pc"] != pc for row in self.records.since(int(state.get("pushed") or 0))
        )
        if state.get("failing_since"):
            kind = "unreachable"
        elif behind:
            kind = "sending"
        elif state.get("ok_at"):
            kind = "ok"
        else:
            kind = "unknown"
        return {
            "state": kind,
            "since": state.get("failing_since"),
            "last_reached": state.get("ok_at"),
            "waiting": behind,
        }

    def listing(self, principal) -> dict[str, Any]:
        """Your PCs as the apps show them. A moderator sees only the PCs
        where they were given something."""
        rows = self.records.all("pc")
        if not rows:
            me = self._me()
            rows = [{"key": self.pc, "body": me}]
        if not principal.is_owner:
            mine = self.db.query_one(
                "SELECT id FROM accounts WHERE username = ?", (principal.user,)
            )
            given = set()
            if mine:
                from .security.auth import _has_access

                for grant in self.records.all("grants"):
                    pc, _, rid = grant["key"].partition(":")
                    if rid == mine["id"] and _has_access(grant["body"]):
                        given.add(pc)
            given.add(self.pc)
            rows = [r for r in rows if r["key"] in given]
        pcs = []
        for row in rows:
            body = row["body"]
            this = row["key"] == self.pc
            pcs.append(
                {
                    "id": row["key"],
                    "name": body.get("name") or "PC",
                    "address": body.get("address"),
                    "port": body.get("port"),
                    "fingerprint": body.get("fingerprint"),
                    "this": this,
                    "reach": {
                        "state": "this",
                        "since": None,
                        "last_reached": None,
                        "waiting": False,
                    }
                    if this
                    else self.reach(row["key"]),
                }
            )
        out: dict[str, Any] = {"this_pc": self.pc, "joined": self.joined(), "pcs": pcs}
        if principal.is_owner:
            out["joins"] = [
                {
                    k: v
                    for k, v in j.items()
                    if k in ("id", "name", "address", "created_at", "expires_at")
                }
                for j in self._joins()
            ]
            out["resets"] = [
                {**r, "name": self._name_of(r["pc"])} for r in self.auth.pending_resets()
            ]
            out["joining"] = bool(self.db.get_setting("pcs_joining"))
        return out

    def _name_of(self, pc: str) -> str:
        found = self.records.get("pc", pc, removed=True)
        return str(found["body"].get("name")) if found else "another PC"

    # -- the loop ------------------------------------------------------------
    def start(self) -> None:
        self._loop = self.auth.loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        if self.joined():
            self._record_me()
        if self.db.get_setting("records_key_remade"):
            self.db.set_setting("records_key_remade", None)
            self.core.bus.publish_soon(
                Event(
                    type="records_tampered",
                    level="error",
                    message="Command Block couldn't open its account key, so it made a new one "
                    "and signed the accounts again. That's expected after moving this PC's "
                    "data. If you didn't, check your moderators and signed-in devices.",
                )
            )
        self._task = asyncio.create_task(self._run(), name="your-pcs")

    async def stop(self) -> None:
        for task in [self._task, *self._tasks]:
            if task is not None:
                task.cancel()
        self._task = None

    def _soon(self) -> None:
        loop, wake = self._loop, self._wake
        if loop is None or wake is None or loop.is_closed():
            return
        try:
            if asyncio.get_running_loop() is loop:
                wake.set()
                return
        except RuntimeError:
            pass
        loop.call_soon_threadsafe(wake.set)

    async def _run(self) -> None:
        assert self._wake is not None
        for peer in self.peers():
            await self._pull(peer)
        while True:
            try:
                await self._round()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("sending account changes failed")
            wait = self._next_wait()
            try:
                await asyncio.wait_for(self._wake.wait(), wait)
            except TimeoutError:
                pass
            self._wake.clear()
            await asyncio.sleep(0.2)  # a few saves in a row go together

    def _next_wait(self) -> float | None:
        """None (sleep until something changes) unless a PC waits for a
        retry or a join waits for Allow."""
        now = time.time()
        times = []
        if self.db.get_setting("pcs_joining"):
            times.append(now + HELLO_EVERY)
        for peer in self.peers():
            state = self._peer_state(peer["key"])
            if self.reach(peer["key"])["waiting"]:
                times.append(max(float(state.get("next") or 0), now))
        return max(min(times) - now, 0.05) if times else None

    async def _round(self) -> None:
        joining = self.db.get_setting("pcs_joining")
        if joining:
            await self._hello(joining)
        now = time.time()
        for peer in self.peers():
            state = self._peer_state(peer["key"])
            if self.reach(peer["key"])["waiting"] and float(state.get("next") or 0) <= now:
                await self._push(peer)

    # -- calling another PC --------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            PC_HEADER: self.pc,
            KEY_HEADER: base64.b64encode(self.records.keys["sync"]).decode(),
        }

    async def _call(
        self, where: dict[str, Any], method: str, path: str, body: Any = None, key: bool = True
    ) -> httpx.Response:
        host, port, fp = where["address"], int(where["port"]), where["fingerprint"]
        context = await asyncio.to_thread(pinned_context, host, port, fp)
        async with httpx.AsyncClient(verify=context, timeout=15) as http:
            return await http.request(
                method,
                f"https://{host}:{port}{path}",
                json=body,
                headers=self._headers() if key else None,
            )

    def _failed(self, pc: str, why: str) -> None:
        state = self._peer_state(pc)
        first = not state.get("failing_since")
        state["failing_since"] = state.get("failing_since") or time.time()
        state["tries"] = int(state.get("tries") or 0) + 1
        state["next"] = time.time() + RETRY[min(state["tries"] - 1, len(RETRY) - 1)]
        self._save_state()
        if first:
            log.info("couldn't reach PC %s: %s", pc, why)

    def _reached(self, pc: str) -> bool:
        """Note that a PC answered. True when it had been out of reach."""
        state = self._peer_state(pc)
        was_out = bool(state.get("failing_since"))
        state.update(failing_since=None, tries=0, next=0, ok_at=time.time())
        self._save_state()
        return was_out

    async def _push(self, peer: dict[str, Any]) -> None:
        pc = peer["key"]
        state = self._peer_state(pc)
        rows = [r for r in self.records.since(int(state["pushed"] or 0)) if r["pc"] != pc]
        last = self.records.last_seq()
        try:
            answer = await self._call(peer["body"], "POST", "/api/sync/changes", {"records": rows})
        except (OSError, httpx.HTTPError) as exc:
            self._failed(pc, str(exc))
            return
        if answer.status_code == 410:
            await self._split("another PC says this PC was removed")
            return
        if answer.status_code != 200:
            self._failed(pc, f"answered {answer.status_code}")
            return
        state["pushed"] = last
        if self._reached(pc):
            await self._pull(peer)

    async def _pull(self, peer: dict[str, Any]) -> None:
        pc = peer["key"]
        state = self._peer_state(pc)
        try:
            answer = await self._call(
                peer["body"], "GET", f"/api/sync/changes?since={int(state['pulled'] or 0)}"
            )
        except (OSError, httpx.HTTPError) as exc:
            self._failed(pc, str(exc))
            return
        if answer.status_code == 410:
            await self._split("another PC says this PC was removed")
            return
        if answer.status_code != 200:
            self._failed(pc, f"answered {answer.status_code}")
            return
        found = answer.json()
        for row in found.get("records") or []:
            self.records.apply(row)
        state["pulled"] = int(found.get("seq") or 0)
        self._reached(pc)

    # -- answering another PC -----------------------------------------------
    def check_peer(self, pc: str, key: str) -> dict[str, Any]:
        """The calling PC's record, when its sync key is right."""
        found = self.records.get("pc", pc or "", removed=True)
        try:
            given = base64.b64decode(key or "", validate=True)
        except ValueError:
            given = b""
        if found is None or pc == self.pc:
            raise Refused(403, "This PC doesn't know that PC.")
        if not hmac.compare_digest(
            hashlib.sha256(given).hexdigest(), str(found["body"].get("sync_key_hash") or "")
        ):
            raise Refused(403, "That PC's sync key isn't right.")
        if found["removed"]:
            raise Refused(410, "That PC was removed from this account.")
        return found

    def receive(self, pc: str, rows: list[dict[str, Any]]) -> int:
        taken = sum(1 for row in rows[:5000] if self.records.apply(row) == "taken")
        state = self._peer_state(pc)
        if state.get("failing_since"):
            # It's back: send it what it missed now, not at the next retry.
            state.update(next=0)
            self._reached(pc)
            self._soon()
        return taken

    def changes_for(self, pc: str, since: int) -> dict[str, Any]:
        rows = [r for r in self.records.since(since) if r["pc"] != pc]
        return {"records": rows, "seq": self.records.last_seq()}

    def hello(self, pc: str) -> dict[str, Any]:
        """A PC the owner allowed asks for the account: the signing key and
        a full copy."""
        self._record_me()
        return {
            "pc": self.pc,
            "signing_key": base64.b64encode(self.records.keys["signing"]).decode(),
            "records": self.records.since(0),
            "seq": self.records.last_seq(),
        }

    # -- joining: on the PC that is already in the account ------------------
    def _codes(self) -> list[dict[str, Any]]:
        now = time.time()
        return [c for c in self.db.get_setting("pcs_join_codes", []) or [] if c["expires_at"] > now]

    def join_link(self) -> dict[str, Any]:
        code = pairing.pairing_code(self.core.config)
        if not code["ok"]:
            raise PcError(code["reason"] or "This PC has no pairing link yet.")
        secret = secrets.token_urlsafe(24)
        expires = time.time() + LINK_SECONDS
        codes = self._codes() + [
            {"hash": hashlib.sha256(secret.encode()).hexdigest(), "expires_at": expires}
        ]
        self.db.set_setting("pcs_join_codes", codes[-5:])
        url = code["url"] + "&" + urlencode({"join": secret})
        return {"ok": True, "url": url, "qr": pairing.qr_rows(url), "expires_at": expires}

    def _joins(self) -> list[dict[str, Any]]:
        now = time.time()
        return [j for j in self.db.get_setting("pcs_joins", []) or [] if j["expires_at"] > now]

    def request_join(self, payload: dict[str, Any], source_ip: str) -> dict[str, Any]:
        """A new PC asks to join. Its owner name and password count as a
        sign-in (same limits and lockout); the code works for 10 minutes."""
        auth = self.auth
        auth.login_rate.check(f"login:{source_ip or 'unknown'}")
        owner = auth.owner()
        name = str(payload.get("username") or "")[:64]
        remaining = auth._locked_out(owner["username"], source_ip)
        if remaining and remaining > 0:
            raise AuthError(
                f"Too many failed attempts. Try again in {int(remaining / 60) + 1} minutes.",
                status=429,
                retry_after=int(remaining),
            )
        given = hashlib.sha256(str(payload.get("code") or "").encode()).hexdigest()
        codes = self._codes()
        code = next((c for c in codes if hmac.compare_digest(c["hash"], given)), None)
        password_ok = verify_password(str(payload.get("password") or ""), owner["password_hash"])
        if code is None or not password_ok or not hmac.compare_digest(name, owner["username"]):
            auth._record_attempt(owner["username"], source_ip, False)
            self.db.audit("pc_join_refused", source_ip=source_ip, result="denied")
            if code is None:
                raise AuthError("That join link has run out or was used. Make a new one.", 403)
            raise AuthError("That username or password isn't right.")
        auth._record_attempt(owner["username"], source_ip, True)
        pc = payload.get("pc") or {}
        try:
            joining: dict[str, Any] = {
                "id": secrets.token_hex(8),
                "pc": str(pc["id"])[:32],
                "name": str(pc.get("name") or "A new PC")[:64],
                "address": str(pc["address"])[:255],
                "port": int(pc["port"]),
                "fingerprint": str(pc["fingerprint"]).lower(),
                "sync_key_hash": hashlib.sha256(
                    base64.b64decode(str(payload["sync_key"]), validate=True)
                ).hexdigest(),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise PcError("The new PC didn't say where it is.") from exc
        if len(joining["fingerprint"]) != 64 or joining["pc"] == self.pc:
            raise PcError("The new PC didn't say where it is.")
        self.db.set_setting("pcs_join_codes", [c for c in codes if c is not code])
        joining.update(
            made=self.records.clock.now(),
            created_at=time.time(),
            expires_at=time.time() + LINK_SECONDS,
        )
        self.db.set_setting("pcs_joins", self._joins() + [joining])
        self.db.audit("pc_join_asked", target=joining["name"], source_ip=source_ip)
        self.core.bus.publish_soon(
            Event(
                type="pc_join_request",
                level="warn",
                message=f"Allow {joining['name']} to join your account?",
                data={"join": joining["id"], "name": joining["name"]},
            )
        )
        return {"ok": True, "waiting": True, "pc": self.pc}

    def answer_join(self, principal, join_id: str, allow: bool) -> dict[str, Any]:
        joins = self._joins()
        found = next((j for j in joins if j["id"] == join_id), None)
        if found is None:
            raise PcError("That request has run out. Ask again from the new PC.")
        if allow:
            made = self.records.get("session", principal.token_hash or "")
            if made is None or made["body"].get("created", "") >= found["made"]:
                raise AuthError(
                    f"Allow it from a device that was signed in before {found['name']} asked.",
                    status=403,
                )
        self.db.set_setting("pcs_joins", [j for j in joins if j["id"] != join_id])
        if not allow:
            self.db.audit("pc_join_refused", user=principal.user, target=found["name"])
            return {"ok": True, "allowed": False}
        self._record_me()
        if self.records.get("pc", self.pc) is None:
            raise PcError("This PC's own address isn't known yet, so the new PC can't reach it.")
        body = {k: found[k] for k in ("name", "address", "port", "fingerprint", "sync_key_hash")}
        self.records.put("pc", found["pc"], body)
        self.db.audit("pc_joined", user=principal.user, target=found["name"])
        self.core.bus.publish_soon(
            Event(
                type="pc_joined",
                level="warn",
                message=f"{found['name']} joined your account",
                data={"name": found["name"]},
            )
        )
        return {"ok": True, "allowed": True}

    # -- joining: on the new PC ------------------------------------------------
    async def join(self, link: str, username: str, password: str) -> dict[str, Any]:
        """Ask the PC the join link is from to take this PC into the account."""
        if self.joined():
            raise PcError("This PC already shares its accounts. Remove it from Your PCs first.")
        where = read_join_link(link)
        me = self._me()
        if not me["address"] or not me["fingerprint"]:
            raise PcError(
                "This PC's own address isn't known yet, so the other PC couldn't reach it. "
                "Finish connecting this PC first, then try again."
            )
        target = {
            "address": where["host"],
            "port": where["port"],
            "fingerprint": where["fingerprint"],
        }
        body = {
            "code": where["code"],
            "username": username,
            "password": password,
            "pc": {"id": self.pc, **{k: me[k] for k in ("name", "address", "port", "fingerprint")}},
            "sync_key": base64.b64encode(self.records.keys["sync"]).decode(),
        }
        try:
            answer = await self._call(target, "POST", "/api/sync/join", body, key=False)
        except OSError as exc:
            raise PcError(
                "Couldn't reach that PC, or its certificate isn't the one in the link. Check "
                "it's on, then make a new link there."
            ) from exc
        except httpx.HTTPError as exc:
            raise PcError("Couldn't reach that PC. Check it's on and try again.") from exc
        if answer.status_code != 200:
            try:
                said = answer.json().get("detail")
            except ValueError:
                said = None
            raise PcError(str(said or f"The other PC said no ({answer.status_code})."))
        self.db.set_setting(
            "pcs_joining",
            {**target, "pc": answer.json().get("pc"), "until": time.time() + LINK_SECONDS},
        )
        self._soon()
        return {"ok": True, "waiting": True}

    async def _hello(self, joining: dict[str, Any]) -> None:
        if time.time() > float(joining.get("until") or 0):
            self.db.set_setting("pcs_joining", None)
            self.core.bus.publish_soon(
                Event(
                    type="pc_join_ran_out",
                    level="warn",
                    message="Nobody pressed Allow in time, so this PC didn't join. Try again.",
                )
            )
            return
        try:
            answer = await self._call(joining, "GET", "/api/sync/hello")
        except (OSError, httpx.HTTPError):
            return
        if answer.status_code != 200:
            return  # not allowed yet
        self._adopt(answer.json())

    def _adopt(self, found: dict[str, Any]) -> None:
        """Allowed: take the account's key and copy. This PC's own owner
        account and its sign-ins give way to the shared one; its moderators
        are kept and sent to the other PCs."""
        key = base64.b64decode(found["signing_key"])
        for row in self.db.query("SELECT token_hash FROM sessions"):
            made = self.records.get("session", row["token_hash"])
            if made and made["body"].get("account") == "owner":
                self.auth._forget_session(row["token_hash"])
        self.records.drop("owner", "owner")
        self.records.drop("owner_reset", self.pc)
        self.records.set_signing_key(key)
        for row in found.get("records") or []:
            self.records.apply(row)
        other = str(found.get("pc") or "")
        self.state[other] = {
            "pushed": 0,
            "pulled": int(found.get("seq") or 0),
            "tries": 0,
            "next": 0,
        }
        self._reached(other)
        self.db.set_setting("pcs_joining", None)
        self._record_me()
        self.core.bus.publish_soon(
            Event(
                type="pc_joined",
                level="warn",
                message="This PC joined your account. Sign in with your account's password.",
            )
        )

    # -- removing --------------------------------------------------------------
    def remove(self, principal, pc: str) -> dict[str, Any]:
        found = self.records.get("pc", pc)
        if found is None:
            raise PcError("That PC isn't in Your PCs.")
        if pc == self.pc:
            raise PcError("Remove this PC from one of your other PCs.")
        self.records.remove("pc", pc)
        self.state.pop(pc, None)
        self._save_state()
        self.db.audit("pc_removed", user=principal.user, target=found["body"].get("name"))
        self.core.bus.publish_soon(
            Event(
                type="pc_removed",
                level="warn",
                message=f"{found['body'].get('name')} was removed from your account",
            )
        )
        # Tell the removed PC too, so it stops at once when it's on.
        task = asyncio.get_running_loop().create_task(self._tell_removed(found))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return {"ok": True}

    async def _tell_removed(self, found: dict[str, Any]) -> None:
        rows = [r for r in self.records.since(0) if r["kind"] == "pc" and r["key"] == found["key"]]
        try:
            await self._call(found["body"], "POST", "/api/sync/changes", {"records": rows})
        except (OSError, httpx.HTTPError):
            pass  # it learns at its next try (it gets a 410)

    def _apply_pc(self, record: dict[str, Any], old: dict[str, Any] | None) -> None:
        if record["removed"] and record["key"] == self.pc:
            task = asyncio.get_running_loop().create_task(self._split("it was removed"))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif record["removed"]:
            self.state.pop(record["key"], None)
            self._save_state()

    async def _split(self, why: str) -> None:
        """Back to its own separate owner account, with the owner password
        it had last (a reset made here, if it waited)."""
        if not self.records.all("pc", removed=True):
            return
        log.warning("this PC no longer shares its accounts: %s", why)
        owner = self.auth.owner()
        self.records.set_signing_key(secrets.token_bytes(32))
        for kind in ("pc", "owner_reset"):
            for row in self.records.all(kind, removed=True):
                self.records.drop(kind, row["key"])
        for row in self.records.all("grants", removed=True):
            if not row["key"].startswith(self.pc + ":"):
                self.records.drop("grants", row["key"])
        self.records.put("owner", "owner", owner)
        self.state = {}
        self._save_state()
        self.core.bus.publish_soon(
            Event(
                type="pc_removed",
                level="warn",
                message="This PC was removed from your account. It has its own owner account "
                "again, with the password it had.",
            )
        )
