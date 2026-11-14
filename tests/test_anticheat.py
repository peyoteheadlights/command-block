"""The anti-cheat picker (agent/anticheat.py) and its Protection card line.

The picker is read through the agent API like the apps read it: free ones
only where they run, paid ones never with a guessed price, the Fabric
warning only when a mod players need is in, and two anti-cheats named as a
problem. Installing goes through the normal install and upload, which have
their own tests.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import anticheat, downloads
from agent.main import create_app
from agent.protection import Facts, lines
from agent.security import permissions as P
from agent.security.auth import hash_password

from .conftest import PASSWORD, build_multi_config
from .test_helpers import add_helper
from .test_moderators import give
from .test_mods import make_jar

BASE = "/api/servers/survival"


def plugin_jar(folder: Path, name: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / f"{name}.jar", "w") as zf:
        zf.writestr("plugin.yml", f"name: {name}\nversion: '1.0'\nmain: x.Main\n")
        zf.writestr("x/Main.class", b"\xca\xfe\xba\xbe")


def line_ids(found):
    return {line["id"]: line for line in found if line["id"].startswith("anticheat_")}


# ------------------------------------------------------------ the card line
def test_the_card_names_what_is_in_and_never_gives_a_verdict():
    props = {"online-mode": "true"}
    assert line_ids(lines(Facts(properties=props))) == {}  # a type none runs on
    none = line_ids(lines(Facts(properties=props, anticheat=[])))
    assert none["anticheat_none"]["level"] == "info"
    community = line_ids(lines(Facts(properties=props, anticheat=[], played_by="community")))
    assert community["anticheat_none"]["level"] == "suggest"
    one = line_ids(lines(Facts(properties=props, anticheat=["Grim"])))
    assert one["anticheat_on"]["values"] == {"name": "Grim"}
    two = line_ids(lines(Facts(properties=props, anticheat=["Grim", "Vulcan"])))
    assert two["anticheat_two"]["level"] == "problem"
    assert two["anticheat_two"]["values"] == {"names": "Grim, Vulcan"}


def test_every_offered_anti_cheat_has_words_and_no_price_is_made_up():
    words = json.loads((Path(__file__).parents[1] / "shared/strings.json").read_text("utf-8"))
    for entry in (*anticheat.FREE, *anticheat.PAID):
        assert f"anticheat.about.{entry['id']}" in words
        assert all(
            t != "bedrock" and t in ("paper", "purpur", "leaf", "fabric") for t in entry["types"]
        )
    # A price is only ever shown together with the month it was checked.
    if any(e["price"] for e in anticheat.PAID):
        assert anticheat.PRICES_CHECKED


# ------------------------------------------------------------ the API
class FakeSpiget:
    """Spiget's resource answer for Vulcan; ``answer`` None: it is down."""

    def __init__(self) -> None:
        self.answer: dict | None = {"premium": True, "price": 19.99, "currency": "usd"}
        self.asked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request.url.path)
        if self.answer is None:
            return httpx.Response(503)
        return httpx.Response(200, content=json.dumps(self.answer).encode())


@pytest.fixture
def spiget(monkeypatch):
    fake = FakeSpiget()
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def agent(tmp_path, monkeypatch, spiget):
    """The real agent: survival is Paper, creative is Vanilla."""
    config = build_multi_config(tmp_path)
    config._data["servers"][0].update(type="paper")
    config._data["servers"][1].update(type="vanilla")
    config._server_cache.clear()
    monkeypatch.setenv("MCSC_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MCSC_ADMIN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))
    monkeypatch.delenv("MCSC_API_TOKEN", raising=False)
    with TestClient(create_app(config)) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        client.headers["Authorization"] = f"Bearer {token.json()['token']}"
        yield client


def picker(client, base=BASE, headers=None):
    response = client.get(f"{base}/anticheat", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def folder(client, server_id="survival") -> Path:
    return Path(client.app.state.core.servers[server_id].config.server_dir)


def test_paper_gets_free_and_paid_ones_and_vanilla_gets_none(agent):
    found = picker(agent)
    assert found["supported"] and found["installed"] == []
    assert [f["project"] for f in found["free"]] == ["grimac", "intave"]
    assert not any(f["warning"] for f in found["free"])
    assert [p["name"] for p in found["paid"]] == ["Vulcan", "Polar"]
    assert all(p["link"].startswith("https://") for p in found["paid"])
    assert found["may_install"] and found["may_add_own_jar"]
    vanilla = picker(agent, "/api/servers/creative")
    assert vanilla == {**vanilla, "supported": False, "free": [], "paid": []}


def test_vulcans_price_is_read_once_a_day_and_kept_when_spiget_is_down(agent, spiget):
    vulcan = picker(agent)["paid"][0]
    assert vulcan["price"] == "19.99 USD" and vulcan["checked"]
    assert spiget.asked == ["/v2/resources/83626"]
    assert picker(agent)["paid"][0]["price"] == "19.99 USD"
    assert len(spiget.asked) == 1, "read at most once a day"
    assert picker(agent)["paid"][1]["price"] is None, "Polar has no price to read"

    db = agent.app.state.core.db
    saved = db.get_setting(anticheat.PRICES_SETTING)
    db.set_setting(anticheat.PRICES_SETTING, {"vulcan": {**saved["vulcan"], "tried_at": 0}})
    spiget.answer = None
    assert picker(agent)["paid"][0]["price"] == "19.99 USD", "the last price read stays"
    assert len(spiget.asked) == 2


@pytest.mark.parametrize(
    "answer",
    [
        {"premium": False, "price": 0, "currency": "usd"},
        {"premium": True, "price": "19.99", "currency": "usd"},
        {"premium": True, "price": 19.99, "currency": "<b>"},
        {"premium": True, "price": 0, "currency": "usd"},
        [],
    ],
)
def test_an_answer_that_isnt_a_clear_price_shows_none(answer):
    assert anticheat._price(answer) is None


def test_two_anti_cheats_are_named_on_the_picker_and_the_card(agent):
    plugin_jar(folder(agent) / "plugins", "GrimAC")
    found = picker(agent)
    assert found["installed"] == ["Grim"] and found["free"][0]["on"]
    plugin_jar(folder(agent) / "plugins", "Vulcan")
    agent.app.state.core.servers["survival"].mods.scan()  # past the 30-second cache
    assert picker(agent)["installed"] == ["Grim", "Vulcan"]
    card = agent.get(f"{BASE}/protection").json()["lines"]
    assert line_ids(card)["anticheat_two"]["values"]["names"] == "Grim, Vulcan"


def test_a_moderator_without_add_own_jar_gets_no_file_button(agent):
    owner = dict(agent.headers)
    agent.headers.pop("Authorization")
    sam = add_helper(agent, owner)
    give(agent, owner, {"survival": P.LOOK_AFTER_IT})
    found = picker(agent, headers=sam)
    assert not found["may_add_own_jar"]


@pytest.fixture
def fabric(tmp_path, monkeypatch, spiget):
    config = build_multi_config(tmp_path)
    config._data["servers"][0].update(type="fabric")
    config._server_cache.clear()
    monkeypatch.setenv("MCSC_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MCSC_ADMIN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))
    monkeypatch.delenv("MCSC_API_TOKEN", raising=False)
    with TestClient(create_app(config)) as client:
        token = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        client.headers["Authorization"] = f"Bearer {token.json()['token']}"
        yield client


def test_grim_on_fabric_warns_only_when_a_mod_players_need_is_in(fabric):
    mods = folder(fabric) / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    make_jar(mods / "lithium.jar", "lithium", environment="server")
    make_jar(mods / "grim.jar", "grimac")  # Grim itself says "*": it doesn't count
    found = picker(fabric)
    assert [f["project"] for f in found["free"]] == ["grimac"] and found["paid"] == []
    assert not found["free"][0]["warning"] and found["installed"] == ["Grim"]
    make_jar(mods / "waystones.jar", "waystones")  # both sides
    fabric.app.state.core.servers["survival"].mods.scan()
    assert picker(fabric)["free"][0]["warning"]
