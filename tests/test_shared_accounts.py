"""Shared accounts across the owner's PCs (agent/yourpcs.py,
agent/security/records.py).

Each "PC" here is a real agent with its own data folder and its own
certificate, served over real HTTPS on localhost in its own thread, so the
PCs talk to each other exactly as they would at home: pinned certificate,
sync key, the agent's normal API.
"""

from __future__ import annotations

import socket
import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import uvicorn

from agent.main import create_app, resolve_tls
from agent.security.auth import hash_password
from agent.security.certs import issue_server_certificate

from .conftest import PASSWORD, build_config

NEW_PASSWORD = "a brand new owner password"
SAM_PASSWORD = "moderator password 1"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class PC:
    """One agent on HTTPS, which can be turned off and on again."""

    def __init__(self, folder, name: str):
        self.name = name
        self.config = build_config(folder)
        made = issue_server_certificate(self.config.cert_dir, ["localhost"], ["127.0.0.1"])
        for key, value in (
            ("tls.enabled", True),
            ("tls.certificate", made["cert_path"]),
            ("tls.private_key", made["key_path"]),
            ("tls.ca_certificate", made["ca_path"]),
            ("tls.hostname", "127.0.0.1"),
            ("network.host", "127.0.0.1"),
            ("network.port", free_port()),
        ):
            self.config.set(key, value)
        self.port = int(self.config.get("network.port"))
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None
        self.app = None

    def on(self) -> PC:
        certfile, keyfile = resolve_tls(self.config)
        self.app = create_app(self.config)
        self.server = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host="127.0.0.1",
                port=self.port,
                ssl_certfile=certfile,
                ssl_keyfile=keyfile,
                log_level="warning",
                access_log=False,
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 30
        while time.time() < deadline and not self.server.started:
            time.sleep(0.05)
        assert self.server.started, f"{self.name} didn't start"
        return self

    def off(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
            assert self.thread is not None
            self.thread.join(timeout=30)
            self.server = None

    @property
    def core(self):
        assert self.app is not None
        return self.app.state.core

    def http(self) -> httpx.Client:
        return httpx.Client(
            base_url=f"https://127.0.0.1:{self.port}",
            verify=str(self.config.tls_ca_certificate),
            timeout=30,
        )

    def call(self, method: str, path: str, token: str | None = None, **kwargs) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with self.http() as client:
            return client.request(method, path, headers=headers, **kwargs)

    def sign_in(self, username: str = "admin", password: str = PASSWORD, phone=False) -> str:
        answer = self.call(
            "POST",
            "/api/auth/login",
            json={
                "username": username,
                "password": password,
                "remember": phone,
                "phone": phone,
                "device": "Phone" if phone else "Desktop",
            },
        )
        assert answer.status_code == 200, answer.text
        return answer.json()["token"]


def wait_for(check, seconds: float = 20.0):
    deadline = time.time() + seconds
    while True:
        found = check()
        if found:
            return found
        if time.time() > deadline:
            raise AssertionError("timed out waiting")
        time.sleep(0.1)


@pytest.fixture
def pcs(tmp_path, monkeypatch):
    monkeypatch.setenv("MCSC_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MCSC_ADMIN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))
    monkeypatch.delenv("MCSC_API_TOKEN", raising=False)
    made: list[PC] = []

    def make(name: str) -> PC:
        pc = PC(tmp_path / name, name).on()
        made.append(pc)
        return pc

    yield make
    for pc in made:
        pc.off()


def join(new: PC, old: PC, old_token: str, password: str = PASSWORD) -> None:
    """``new`` joins the account ``old`` is in, allowed from ``old_token``."""
    link = old.call("POST", "/api/pcs/link", old_token).json()["url"]
    new_token = new.sign_in()
    asked = new.call(
        "POST",
        "/api/pcs/join",
        new_token,
        json={"link": link, "username": "admin", "password": password},
    )
    assert asked.status_code == 200, asked.text
    joins = old.call("GET", "/api/pcs", old_token).json()["joins"]
    allowed = old.call("POST", f"/api/pcs/joins/{joins[0]['id']}/allow", old_token)
    assert allowed.status_code == 200, allowed.text
    wait_for(lambda: new.core.pcs.joined() and not new.core.db.get_setting("pcs_joining"))


def names(pc: PC) -> set[str]:
    return {row["username"] for row in pc.core.auth.accounts()}


def add_sam(pc: PC, token: str, servers=None, name: str = "sam") -> None:
    made = pc.call(
        "POST",
        "/api/accounts",
        token,
        json={"username": name, "password": SAM_PASSWORD, "servers": servers or ["test"]},
    )
    assert made.status_code == 200, made.text


# ------------------------------------------------------------------ joining
def test_join_needs_allow_from_a_device_signed_in_before(pcs):
    a, b = pcs("a"), pcs("b")
    owner_a = a.sign_in()
    link = a.call("POST", "/api/pcs/link", owner_a).json()["url"]
    assert parse_qs(urlparse(link).query)["join"]
    token_b = b.sign_in()
    wrong = b.call(
        "POST",
        "/api/pcs/join",
        token_b,
        json={"link": link, "username": "admin", "password": "nope nope nope"},
    )
    assert wrong.status_code == 400 and "isn't right" in wrong.json()["detail"]
    asked = b.call(
        "POST",
        "/api/pcs/join",
        token_b,
        json={"link": link, "username": "admin", "password": PASSWORD},
    )
    assert asked.status_code == 200, asked.text
    listed = a.call("GET", "/api/pcs", owner_a).json()
    assert listed["joins"][0]["name"]
    # Someone who just learned the password signs in on A and tries Allow.
    newcomer = a.sign_in()
    refused = a.call("POST", f"/api/pcs/joins/{listed['joins'][0]['id']}/allow", newcomer)
    assert refused.status_code == 403
    # Without Allow, B never gets in: its sync key isn't known to A.
    time.sleep(1)
    assert not b.core.pcs.joined()
    assert (
        a.call("POST", f"/api/pcs/joins/{listed['joins'][0]['id']}/allow", owner_a).status_code
        == 200
    )
    wait_for(lambda: b.core.pcs.joined())
    alerts = [e["type"] for e in a.core.db.query("SELECT type FROM events")]
    assert "pc_join_request" in alerts and "pc_joined" in alerts
    # The link worked once.
    again = b.call(
        "POST",
        "/api/pcs/join",
        b.sign_in(),
        json={"link": link, "username": "admin", "password": PASSWORD},
    )
    assert again.status_code == 400


def test_a_join_link_runs_out_after_10_minutes(pcs, monkeypatch):
    a, b = pcs("a"), pcs("b")
    owner_a = a.sign_in()
    link = a.call("POST", "/api/pcs/link", owner_a).json()["url"]
    codes = a.core.db.get_setting("pcs_join_codes")
    a.core.db.set_setting("pcs_join_codes", [{**c, "expires_at": time.time() - 1} for c in codes])
    asked = b.call(
        "POST",
        "/api/pcs/join",
        b.sign_in(),
        json={"link": link, "username": "admin", "password": PASSWORD},
    )
    assert asked.status_code == 400 and "run out" in asked.json()["detail"]


def test_one_sign_in_works_on_every_pc_and_sign_out_everywhere_means_every_pc(pcs):
    a, b = pcs("a"), pcs("b")
    owner_a = a.sign_in()
    join(b, a, owner_a)
    phone = a.sign_in(phone=True)
    wait_for(lambda: b.call("GET", "/api/servers", phone).status_code == 200)
    # B lists A and itself.
    listed = b.call("GET", "/api/pcs", phone).json()
    assert listed["joined"] and len(listed["pcs"]) == 2
    revoked = b.call("POST", "/api/security/revoke-sessions", phone)
    assert revoked.status_code == 200
    wait_for(lambda: a.call("GET", "/api/servers", phone).status_code == 401)
    wait_for(lambda: a.call("GET", "/api/servers", owner_a).status_code == 401)


def test_the_new_pcs_own_owner_gives_way_to_the_account(pcs):
    a, b = pcs("a"), pcs("b")
    b.core.auth.reset_owner("admin", hash_password("b had its own password"))
    owner_a = a.sign_in()
    link = a.call("POST", "/api/pcs/link", owner_a).json()["url"]
    token_b = b.sign_in(password="b had its own password")
    b.call(
        "POST",
        "/api/pcs/join",
        token_b,
        json={"link": link, "username": "admin", "password": PASSWORD},
    )
    join_id = a.call("GET", "/api/pcs", owner_a).json()["joins"][0]["id"]
    a.call("POST", f"/api/pcs/joins/{join_id}/allow", owner_a)
    wait_for(lambda: b.core.pcs.joined())
    # B's own sign-in ended; the account's password works there now.
    assert b.call("GET", "/api/servers", token_b).status_code == 401
    b.sign_in()


# ------------------------------------------------------------------ syncing
def test_moderators_sync_and_a_pc_that_was_off_catches_up(pcs):
    a, b = pcs("a"), pcs("b")
    owner_a = a.sign_in()
    join(b, a, owner_a)
    add_sam(a, owner_a)
    wait_for(lambda: "sam" in names(b))
    b.sign_in("sam", SAM_PASSWORD)  # one moderator sign-in on every PC

    b.off()
    a.call("DELETE", "/api/accounts/sam", owner_a)
    add_sam(a, owner_a, name="kim")
    listed = a.call("GET", "/api/pcs", owner_a).json()
    other = next(p for p in listed["pcs"] if not p["this"])
    assert other["reach"]["waiting"] is True
    assert wait_for(
        lambda: (
            a.call("GET", "/api/pcs", owner_a).json()["pcs"]
            and next(p for p in a.call("GET", "/api/pcs", owner_a).json()["pcs"] if not p["this"])[
                "reach"
            ]["state"]
            == "unreachable"
        )
    )
    b.on()
    # The removed marker beats B's older copy, and B gets kim.
    wait_for(lambda: names(b) == {"kim"})
    wait_for(
        lambda: (
            next(p for p in a.call("GET", "/api/pcs", owner_a).json()["pcs"] if not p["this"])[
                "reach"
            ]["state"]
            == "ok"
        )
    )


def test_a_tampered_record_is_refused_with_an_alert(pcs):
    a, b = pcs("a"), pcs("b")
    owner_a = a.sign_in()
    join(b, a, owner_a)
    add_sam(a, owner_a)
    wait_for(lambda: "sam" in names(b))
    row = next(r for r in a.core.auth.records.since(0) if r["kind"] == "account")
    forged = {
        **row,
        "body": row["body"].replace(
            row["body"].split('"password_hash":"')[1].split('"')[0],
            hash_password("an attackers pass"),
        ),
        "hlc": "9999999999999.00000.zz",
    }
    assert b.core.auth.records.apply(forged) == "refused"
    wait_for(lambda: b.core.db.query("SELECT 1 FROM events WHERE type = 'records_tampered'"))
    # Changed straight in B's database file: the sign-in is refused.
    b.core.db.execute(
        "UPDATE accounts SET password_hash = ? WHERE username = 'sam'",
        (hash_password("an attackers pass"),),
    )
    answer = b.call(
        "POST", "/api/auth/login", json={"username": "sam", "password": "an attackers pass"}
    )
    assert answer.status_code == 401


def test_two_moderators_with_one_name_become_sam_and_sam2(pcs):
    a, b = pcs("a"), pcs("b")
    owner_a = a.sign_in()
    join(b, a, owner_a)
    b.off()
    add_sam(a, owner_a)  # the older sam
    a.off()
    b.on()
    add_sam(b, b.sign_in())  # made apart: the newer one
    a.on()
    wait_for(lambda: names(a) == {"sam", "sam2"} and names(b) == {"sam", "sam2"}, 30)
    older = a.core.db.query_one("SELECT id FROM accounts WHERE username = 'sam'")
    assert b.core.db.query_one("SELECT id FROM accounts WHERE username = 'sam'") == older
    wait_for(lambda: a.core.db.query("SELECT 1 FROM events WHERE type = 'moderator_renamed'"))


# ------------------------------------------------------------------ permissions
def test_each_pc_owns_its_servers_permissions_with_three_pcs(pcs):
    a, b, c = pcs("a"), pcs("b"), pcs("c")
    owner_a = a.sign_in()
    join(b, a, owner_a)
    join(c, a, owner_a)
    add_sam(a, owner_a)
    wait_for(lambda: "sam" in names(b) and "sam" in names(c))
    owner_b = wait_for(lambda: b.sign_in())
    given = b.call("PUT", "/api/accounts/sam", owner_b, json={"servers": ["test"]})
    assert given.status_code == 200, given.text
    sam = c.sign_in("sam", SAM_PASSWORD)
    # The moderator's PC list names the PCs where they were given something.
    wait_for(lambda: len(c.call("GET", "/api/pcs", sam).json()["pcs"]) == 3)
    assert c.call("GET", "/api/servers", sam).status_code == 403  # nothing on C
    # A grants row for A written by B is refused.
    row = next(
        r
        for r in b.core.auth.records.since(0)
        if r["kind"] == "grants" and r["pc"] == b.core.pcs.pc
    )
    stolen = {**row, "key": row["key"].replace(b.core.pcs.pc, a.core.pcs.pc, 1)}
    assert a.core.auth.records.apply(stolen) == "refused"
    # Off their last server on A: B still has them, so they stay.
    wait_for(lambda: a.core.auth.can_see_anything_elsewhere("sam"))
    a.call("PUT", "/api/accounts/sam", owner_a, json={"grants": {"test": []}})
    assert "sam" in names(a)
    # Off B's too, once B has heard about A: gone everywhere.
    wait_for(lambda: not b.core.auth.can_see_anything_elsewhere("sam"))
    assert (
        b.call("PUT", "/api/accounts/sam", owner_b, json={"grants": {"test": []}}).status_code
        == 200
    )
    wait_for(lambda: "sam" not in names(a) and "sam" not in names(c))


# ------------------------------------------------------------------ removing
def test_remove_cuts_the_pc_off_and_it_works_alone(pcs):
    a, b, c = pcs("a"), pcs("b"), pcs("c")
    owner_a = a.sign_in()
    join(b, a, owner_a)
    join(c, a, owner_a)
    b_id = b.core.pcs.pc
    wait_for(lambda: c.core.auth.records.get("pc", b_id) is not None)
    assert a.call("DELETE", f"/api/pcs/{b_id}", owner_a).status_code == 200
    # B hears and goes back to its own account; A and C refuse its key.
    wait_for(lambda: not b.core.pcs.joined())
    wait_for(lambda: c.core.auth.records.get("pc", b_id) is None)
    with httpx.Client(verify=False) as raw:
        for pc in (a, c):
            answer = raw.get(
                f"https://127.0.0.1:{pc.port}/api/sync/changes",
                headers=b.core.pcs._headers(),
            )
            assert answer.status_code == 410
    token_b = b.sign_in()  # the last owner password, on its own
    assert b.call("GET", "/api/pcs", token_b).json()["joined"] is False
    wait_for(lambda: a.core.db.query("SELECT 1 FROM events WHERE type = 'pc_removed'"))


# ------------------------------------------------------------------ password reset
def test_a_reset_on_one_pc_waits_for_a_confirm_from_another(pcs):
    a, b = pcs("a"), pcs("b")
    owner_a = a.sign_in()
    join(b, a, owner_a)
    b.core.auth.reset_owner("admin", hash_password(NEW_PASSWORD))
    b.sign_in(password=NEW_PASSWORD)  # works on B at once
    time.sleep(1)
    refused = a.call(
        "POST", "/api/auth/login", json={"username": "admin", "password": NEW_PASSWORD}
    )
    assert refused.status_code == 401  # not on A
    resets = wait_for(lambda: a.call("GET", "/api/pcs", owner_a).json()["resets"])
    assert resets[0]["pc"] == b.core.pcs.pc
    late = a.sign_in()  # signed in after the reset: can't confirm it
    assert a.call("POST", f"/api/pcs/resets/{resets[0]['pc']}/confirm", late).status_code == 403
    done = a.call("POST", f"/api/pcs/resets/{resets[0]['pc']}/confirm", owner_a)
    assert done.status_code == 200, done.text
    a.sign_in(password=NEW_PASSWORD)
    assert (
        a.call(
            "POST", "/api/auth/login", json={"username": "admin", "password": PASSWORD}
        ).status_code
        == 401
    )
    wait_for(
        lambda: (
            b.call(
                "POST", "/api/auth/login", json={"username": "admin", "password": PASSWORD}
            ).status_code
            == 401
        )
    )


# ------------------------------------------------------------------ alerts
def test_alerts_say_which_pc_once_joined(pcs):
    a, b = pcs("a"), pcs("b")
    from agent.events import Event

    event = Event(type="backup_failed", message="x", server_id="test")
    before = a.core.notifier.title(event)[1]
    join(b, a, a.sign_in())
    assert a.core.notifier.title(event)[1] == f"{a.core.pcs.name()}: {before}"


# ------------------------------------------------------------------ the database file
async def test_a_database_other_accounts_can_open_is_an_alert(tmp_path, monkeypatch):
    from agent import core as core_module
    from agent.security import certs

    agent = core_module.AgentCore(build_config(tmp_path))
    told = []
    agent.bus.subscribe(lambda event: told.append(event.type))
    monkeypatch.setattr(certs, "folder_access", lambda _p: ("shared", "Users can open it"))
    await agent._check_database_access(windows=True)
    monkeypatch.setattr(certs, "folder_access", lambda _p: ("private", "fine"))
    await agent._check_database_access(windows=True)
    assert told.count("database_shared") == 1
    await agent.jobs.stop()
    agent.db.close()
