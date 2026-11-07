"""Passwords are hashed with scrypt (one of OWASP's settings), and an older
hash (PBKDF2, or scrypt with less work) keeps working and is re-made the
next time that person signs in."""

import time

from agent.security import auth

from .conftest import PASSWORD

HELPER_PASSWORD = "helper password 123"


def test_scrypt_uses_owasps_setting_and_stays_quick(monkeypatch):
    monkeypatch.setattr(auth, "SCRYPT_N", 2**15)
    monkeypatch.setattr(auth, "SCRYPT_P", 3)
    started = time.perf_counter()
    encoded = auth.hash_password("long enough password")
    took = time.perf_counter() - started
    assert encoded.split("$")[:4] == ["scrypt", "32768", "8", "3"]
    assert auth.verify_password("long enough password", encoded)
    assert not auth.verify_password("long enough passwore", encoded)
    # A weak mini PC is about three times slower than a test machine.
    assert took < 1.0


def test_the_pbkdf2_round_count_is_still_owasps_figure():
    assert auth.OWASP_PBKDF2_SHA256_ROUNDS == 600_000
    encoded = auth.hash_password("long enough password", rounds=1000)
    assert auth.verify_password("long enough password", encoded)


def test_needs_rehash(monkeypatch):
    assert auth.needs_rehash(auth.hash_password(PASSWORD, rounds=1000))
    monkeypatch.setattr(auth, "SCRYPT_N", 32)
    assert not auth.needs_rehash(auth.hash_password(PASSWORD))
    monkeypatch.setattr(auth, "SCRYPT_N", 64)
    assert auth.needs_rehash(auth.hash_password(PASSWORD, salt=b"x" * 16).replace("$64$", "$32$"))
    assert not auth.needs_rehash("not a hash")
    assert not auth.needs_rehash("")


def test_a_hash_asking_for_huge_memory_is_refused():
    huge = "scrypt$1048576$8$1$c2FsdHNhbHRzYWx0c2FsdA==$aGFzaA=="
    assert not auth.verify_password(PASSWORD, huge)


def test_old_owner_hash_signs_in_and_is_rehashed(client):
    core = client.app.state.core
    assert core.auth.owner()["password_hash"].startswith("pbkdf2_sha256$")
    response = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 200, response.text
    saved = core.auth.owner()["password_hash"]
    assert saved.startswith("scrypt$")
    assert auth.verify_password(PASSWORD, saved)
    # The same password: the sign-in just made still works, and so does a new one.
    token = response.json()["token"]
    assert (
        client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    )
    again = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert again.status_code == 200


def test_wrong_password_never_rehashes(client):
    core = client.app.state.core
    before = core.auth.owner()["password_hash"]
    response = client.post("/api/auth/login", json={"username": "admin", "password": "wrong one!!"})
    assert response.status_code == 401
    assert core.auth.owner()["password_hash"] == before


def test_the_owner_moves_out_of_env_into_a_signed_record(client):
    core = client.app.state.core
    record = core.auth.records.get("owner", "owner")
    assert record is not None and record["body"]["username"] == "admin"
    # Changed straight in the database file: refused, never used.
    core.db.execute(
        "UPDATE sync_records SET body = ? WHERE kind = 'owner'",
        (
            '{"changed":"","password_hash":"'
            + auth.hash_password("an attackers pass")
            + '","username":"admin"}',
        ),
    )
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "an attackers pass"}
    )
    assert response.status_code == 503
    assert "safety check" in response.json()["detail"]


def test_old_helper_hash_is_rehashed(multi_client, monkeypatch):
    client = multi_client
    owner = dict(client.headers)
    made = client.post(
        "/api/accounts", json={"username": "sam", "password": HELPER_PASSWORD}, headers=owner
    )
    assert made.status_code == 200, made.text
    db = client.app.state.core.db
    before = db.query_one("SELECT password_hash FROM accounts WHERE username = 'sam'")
    assert before["password_hash"].startswith("scrypt$16$")

    monkeypatch.setattr(auth, "SCRYPT_N", 32)
    response = client.post("/api/auth/login", json={"username": "sam", "password": HELPER_PASSWORD})
    assert response.status_code == 200, response.text
    after = db.query_one("SELECT password_hash FROM accounts WHERE username = 'sam'")
    assert after["password_hash"].startswith("scrypt$32$")
    assert auth.verify_password(HELPER_PASSWORD, after["password_hash"])
    # and the moderator can still sign in: the record moved with the row
    again = client.post("/api/auth/login", json={"username": "sam", "password": HELPER_PASSWORD})
    assert again.status_code == 200
