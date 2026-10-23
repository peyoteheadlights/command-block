"""Tailscale share links made by the agent: a key saved
once, checked with Tailscale first, used for share links, removed for good."""

import asyncio
import json
import time

import httpx
import pytest

from agent import tailscale_share
from agent.api.routes import security as security_routes

ADDRESS = "100.64.0.5"
TOKEN = "tskey-api-kABC123CNTRL-secretpart"


@pytest.fixture
def tailscale(multi_client, monkeypatch):
    return mock_tailscale_api(monkeypatch)


def mock_tailscale_api(monkeypatch):
    """This PC on Tailscale, and Tailscale's API answered by a mock."""
    monkeypatch.setattr(
        security_routes,
        "tailscale_status",
        lambda: {"connected": True, "addresses": [ADDRESS, "fd7a::1"]},
    )
    calls = []
    answers = {"invite": (200, [{"inviteUrl": "https://login.tailscale.com/admin/invite/abc"}])}

    def handler(request):
        calls.append(request)
        path = request.url.path
        if path == "/api/v2/oauth/token":
            return httpx.Response(200, json={"access_token": "oauth-access"})
        if path == "/api/v2/tailnet/-/devices":
            return httpx.Response(
                200, json={"devices": [{"id": "1", "nodeId": "nPC", "addresses": [ADDRESS]}]}
            )
        if path == "/api/v2/tailnet/-/keys/kABC123CNTRL":
            return httpx.Response(200, json={"expires": "2026-12-01T00:00:00Z"})
        if path == "/api/v2/device/nPC/device-invites":
            return httpx.Response(answers["invite"][0], json=answers["invite"][1])
        return httpx.Response(404)

    monkeypatch.setattr(tailscale_share, "TRANSPORT", httpx.MockTransport(handler))
    return {"calls": calls, "answers": answers}


def save_token(client, secret=TOKEN):
    return client.put("/api/tailscale/share-key", json={"kind": "token", "secret": secret})


def test_an_api_token_is_checked_saved_and_its_end_date_kept(multi_client, tailscale):
    response = save_token(multi_client)
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["saved"] is True and state["kind"] == "token"
    assert state["expires_at"] == 1796083200.0  # 2026-12-01, as Tailscale said
    # The key is never sent back.
    assert TOKEN not in json.dumps(multi_client.get("/api/tailscale/share-key").json())


def test_a_share_link_is_made_for_this_pc(multi_client, tailscale):
    save_token(multi_client)
    link = multi_client.post("/api/tailscale/share-link")
    assert link.status_code == 200, link.text
    assert link.json()["url"] == "https://login.tailscale.com/admin/invite/abc"
    invite = tailscale["calls"][-1]
    assert invite.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(invite.content) == [{"multiUse": False, "allowExitNode": False}]


def test_an_oauth_client_gets_a_short_token_first(multi_client, tailscale):
    response = multi_client.put(
        "/api/tailscale/share-key",
        json={"kind": "oauth", "client_id": "kCLIENT", "secret": "tskey-client-kCLIENT-secret"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["expires_at"] is None  # kept until revoked
    multi_client.post("/api/tailscale/share-link")
    assert tailscale["calls"][-1].headers["Authorization"] == "Bearer oauth-access"


def test_a_wrong_key_is_refused_and_not_kept(multi_client, tailscale):
    assert save_token(multi_client, "not-a-tailscale-key").status_code == 400
    assert multi_client.get("/api/tailscale/share-key").json()["saved"] is False


def test_remove_key_deletes_it_and_says_where_to_revoke_it(multi_client, tailscale):
    save_token(multi_client)
    removed = multi_client.delete("/api/tailscale/share-key").json()
    assert removed["saved"] is False
    assert removed["revoke_page"] == "https://login.tailscale.com/admin/settings/keys"
    core = multi_client.app.state.core
    assert not tailscale_share.key_path(core.config).exists()
    assert multi_client.post("/api/tailscale/share-link").status_code == 400


def test_tailscale_refusing_the_link_is_said_plainly(multi_client, tailscale):
    save_token(multi_client)
    tailscale["answers"]["invite"] = (403, {"message": "no"})
    response = multi_client.post("/api/tailscale/share-link")
    assert response.status_code == 400 and "error 403" in response.json()["detail"]


def test_a_token_running_out_within_a_week_is_alerted_once(multi_client, tailscale):
    save_token(multi_client)
    core = multi_client.app.state.core
    path = tailscale_share.key_path(core.config)
    key = json.loads(path.read_text())
    path.write_text(json.dumps({**key, "expires_at": time.time() + 3 * 86400}))
    published = []

    async def record(event):
        published.append(event.type)

    core.bus.subscribe(record)
    asyncio.run(core.tailscale.remind_key())
    asyncio.run(core.tailscale.remind_key())
    assert published.count("tailscale_key_expiring") == 1
