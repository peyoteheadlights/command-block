"""The installer's one-time sign-in: "Open Command Block" signs the desktop
app in as the owner without the password, once, from this PC only, within
10 minutes."""

import json
import time

from fastapi.testclient import TestClient

from agent.security import auth


def on_this_pc(client):
    """Requests from 127.0.0.1, like the desktop app on the agent's PC."""
    return TestClient(client.app, client=("127.0.0.1", 50000))


def a_code(client) -> str:
    return auth.write_one_time_sign_in(client.app.state.core.config.data_dir)


def test_the_code_signs_the_owner_in_once(client):
    code = a_code(client)
    here = on_this_pc(client)
    answer = here.post("/api/auth/once", json={"code": code, "device": "Desktop app"})
    assert answer.status_code == 200
    signed = answer.json()
    assert signed["role"] == "owner" and signed["expires_at"] is None  # kept until signed out
    me = here.get("/api/auth/me", headers={"Authorization": f"Bearer {signed['token']}"})
    assert me.json()["role"] == "owner"
    again = here.post("/api/auth/once", json={"code": code, "device": "Desktop app"})
    assert again.status_code == 401


def test_only_from_this_pc(client):
    code = a_code(client)
    answer = client.post("/api/auth/once", json={"code": code})  # from "testclient"
    assert answer.status_code == 403
    # Still usable from this PC: the refused try didn't spend it.
    assert on_this_pc(client).post("/api/auth/once", json={"code": code}).status_code == 200


def test_only_within_ten_minutes(client, monkeypatch):
    code = a_code(client)
    later = time.time() + 10 * 60 + 1
    monkeypatch.setattr(auth.time, "time", lambda: later)
    answer = on_this_pc(client).post("/api/auth/once", json={"code": code})
    assert answer.status_code == 401
    assert "password" in answer.json()["detail"]


def test_a_wrong_code_fails(client):
    a_code(client)
    answer = on_this_pc(client).post("/api/auth/once", json={"code": "x" * 43})
    assert answer.status_code == 401


def test_only_the_hash_and_expiry_are_stored(client):
    data_dir = client.app.state.core.config.data_dir
    code = a_code(client)
    stored = (data_dir / auth.ONE_TIME_FILE).read_text(encoding="utf-8")
    assert code not in stored
    assert set(json.loads(stored)) == {"hash", "expires_at"}


def test_once_even_when_the_file_cant_be_deleted(client, monkeypatch):
    """The installer made the file as Administrator; the agent's account may
    not be able to delete it."""
    from pathlib import Path

    code = a_code(client)

    def refuse(self, missing_ok=False):
        raise PermissionError("Access is denied")

    monkeypatch.setattr(Path, "unlink", refuse)
    here = on_this_pc(client)
    assert here.post("/api/auth/once", json={"code": code}).status_code == 200
    assert here.post("/api/auth/once", json={"code": code}).status_code == 401
