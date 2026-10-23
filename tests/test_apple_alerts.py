"""iPhone lock-screen alerts through Apple's push service (agent/notifications/apns.py),
and putting the Apple key on the PC (installer/setup_phone_alerts.py)."""

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from agent.notifications import apns
from installer.setup_phone_alerts import install_apple_key, main

from .test_helpers import add_helper
from .test_phone_app import crash, register_phone, send

IPHONE = "ab" * 32  # Apple's push addresses are hex


@pytest.fixture
def owner(multi_client):
    return dict(multi_client.headers)


def apple_key_file(tmp_path, name="AuthKey_ABC123DEFG.p8"):
    key = ec.generate_private_key(ec.SECP256R1())
    path = tmp_path / name
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return path, key


@pytest.fixture
def apple(multi_client, tmp_path, monkeypatch):
    """An Apple key on the PC, and Apple answered by a mock that records
    every request."""
    path, key = apple_key_file(tmp_path)
    install_apple_key(multi_client.app.state.core.config, path, "TEAM123456")
    calls = []
    answers = {"send": (200, {})}

    def handler(request):
        calls.append(request)
        status, body = answers["send"]
        return httpx.Response(status, json=body)

    monkeypatch.setattr(apns, "TRANSPORT", httpx.MockTransport(handler))
    return {"calls": calls, "answers": answers, "key": key}


def register_iphone(client, headers=None):
    response = client.put(
        "/api/app/phone",
        json={"token": IPHONE, "platform": "ios", "label": "iPhone"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def test_an_iphone_is_woken_through_apple_without_any_words(multi_client, apple):
    state = register_iphone(multi_client)
    assert state["configured"] is True and state["registered"] is True
    core = multi_client.app.state.core
    send(core, crash())
    [request] = apple["calls"]
    assert request.url == f"https://api.push.apple.com/3/device/{IPHONE}"
    assert request.headers["apns-topic"] == apns.TOPIC
    assert request.headers["apns-push-type"] == "alert"
    body = json.loads(request.content)
    alert_id = core.db.query_one("SELECT MAX(id) AS id FROM app_alerts")["id"]
    assert body["alert"] == str(alert_id) and body["aps"]["mutable-content"] == 1
    for secret in ("Survival", "survival", "crash", "It crashed"):
        assert secret not in request.content.decode()
    [row] = core.db.query("SELECT last_result FROM app_phones")
    assert row["last_result"] == "ok"


def test_the_sign_in_token_is_signed_with_the_key(multi_client, apple):
    register_iphone(multi_client)
    send(multi_client.app.state.core, crash())
    send(multi_client.app.state.core, crash("creative"))
    first, second = apple["calls"]
    token = first.headers["authorization"].removeprefix("bearer ")
    assert second.headers["authorization"] == first.headers["authorization"]  # reused
    header, claims, signature = token.split(".")
    raw = unb64(signature)
    apple["key"].public_key().verify(
        encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
        f"{header}.{claims}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )
    assert json.loads(unb64(header)) == {"alg": "ES256", "kid": "ABC123DEFG"}
    assert json.loads(unb64(claims))["iss"] == "TEAM123456"


def test_android_phones_still_go_to_google_and_iphones_to_apple(multi_client, owner, apple):
    """Without a Firebase key the Android phone gets nothing; the iPhone
    is woken all the same."""
    helper = add_helper(multi_client, owner)
    multi_client.headers.pop("Authorization")
    register_phone(multi_client, headers=helper)
    register_iphone(multi_client, headers=owner)
    send(multi_client.app.state.core, crash())
    assert [c.url.host for c in apple["calls"]] == ["api.push.apple.com"]


def test_an_iphone_apple_says_is_gone_is_forgotten(multi_client, apple):
    register_iphone(multi_client)
    apple["answers"]["send"] = (410, {"reason": "Unregistered"})
    core = multi_client.app.state.core
    send(core, crash())
    assert core.db.query("SELECT * FROM app_phones") == []


def test_a_development_build_address_is_kept_with_a_hint(multi_client, apple):
    register_iphone(multi_client)
    apple["answers"]["send"] = (400, {"reason": "BadDeviceToken"})
    core = multi_client.app.state.core
    send(core, crash())
    [row] = core.db.query("SELECT last_result FROM app_phones")
    assert "development build" in row["last_result"]


def test_a_refused_sign_in_token_is_made_again(multi_client, apple):
    register_iphone(multi_client)
    apple["answers"]["send"] = (403, {"reason": "ExpiredProviderToken"})
    core = multi_client.app.state.core
    send(core, crash())
    assert core.notifier.apns._token is None


def test_the_sandbox_switch_sends_to_apples_development_address(multi_client, apple, tmp_path):
    path, _key = apple_key_file(tmp_path)
    install_apple_key(multi_client.app.state.core.config, path, "TEAM123456", sandbox=True)
    register_iphone(multi_client)
    send(multi_client.app.state.core, crash())
    assert apple["calls"][0].url.host == "api.sandbox.push.apple.com"


def test_without_the_apple_key_nothing_is_sent(multi_client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        apns, "TRANSPORT", httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(500))
    )
    assert multi_client.get("/api/app/phone?platform=ios").json()["configured"] is False
    state = register_iphone(multi_client)
    assert state["configured"] is False and state["registered"] is True
    send(multi_client.app.state.core, crash())
    assert calls == []


def test_an_iphone_address_that_isnt_hex_is_refused(multi_client):
    response = multi_client.put(
        "/api/app/phone", json={"token": "a/b" + "c" * 30, "platform": "ios", "label": ""}
    )
    assert response.status_code == 422
    response = multi_client.put(
        "/api/app/phone", json={"token": "g" * 64, "platform": "ios", "label": ""}
    )
    assert response.status_code == 422


def test_apple_is_reached_over_http2():
    # h2 is what httpx needs for http2=True; without it this raises.
    httpx.AsyncClient(http2=True)


# ------------------------------------------------------------ the setup command
@pytest.fixture
def config(config, monkeypatch):
    """The setup command run against the test's own data folder."""
    import installer.setup_phone_alerts as setup

    monkeypatch.setattr(setup.Config, "load", lambda path=None: config)
    monkeypatch.setattr(setup, "use_data_in_use", lambda _config: None)
    return config


def test_the_setup_command_keeps_the_apple_key_in_the_data_folder(config, tmp_path, capsys):
    path, _key = apple_key_file(tmp_path)
    assert main(["--apple", str(path), "--team", "team123456"]) == 0
    assert "ABC123DEFG" in capsys.readouterr().out
    loaded = apns.load_key(config)
    assert (loaded.key_id, loaded.team_id, loaded.sandbox) == ("ABC123DEFG", "TEAM123456", False)
    assert apns.key_path(config).read_bytes() == path.read_bytes()
    assert main(["--remove"]) == 0
    assert not apns.configured(config)


@pytest.mark.parametrize(
    ("name", "content", "extra", "words"),
    [
        ("AuthKey_ABC123DEFG.p8", b"not a key", [], "isn't an Apple push key"),
        ("renamed.p8", None, [], "--key-id"),
        ("AuthKey_ABC123DEFG.p8", None, ["--team", "short"], "team ID"),
    ],
)
def test_the_setup_command_refuses_what_isnt_an_apple_key(
    config, tmp_path, capsys, name, content, extra, words
):
    path, _key = apple_key_file(tmp_path, name)
    if content is not None:
        path.write_bytes(content)
    team = extra or ["--team", "TEAM123456"]
    assert main(["--apple", str(path), *team]) == 1
    assert words in capsys.readouterr().out
    assert not apns.configured(config)
