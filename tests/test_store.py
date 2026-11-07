"""The Store (agent/store.py), driven through the agent API like the apps
do: who gets a Store tab, connecting Tebex (key checked, kept, never shown),
the plugin and LuckPerms installed with a backup, the Store ranks, the
checklist, "Check my store", and /store and the Store rank roles in Discord.

Tebex is a fake Game Server API through the downloader's swappable
transport; Modrinth is the add-on suggestions' fake. No network.
"""

from __future__ import annotations

import json
import logging
import time

import httpx
import pytest
import yaml

from agent import downloads, store
from agent.discord import layout
from tests.fake_discord import GUILD_ID, MEMBER_ID
from tests.test_discord import agent, core_of, fake, run, set_up  # noqa: F401
from tests.test_discord_levels import ALEX, linked
from tests.test_helpers import add_helper
from tests.test_suggestions import NEW, PROJECTS, ctx_of, make_client

KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
ROLE = "2000000000000000002"  # "Friends of Sam" in the fake Discord


class FakeTebex:
    """Tebex's Game Server API: /information and /packages, for one key."""

    def __init__(self, online_mode=True, packages=None) -> None:
        self.online_mode = online_mode
        self.packages = packages or []
        self.calls: list[tuple[str, str, str | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.headers.get("x-tebex-secret")))
        if request.headers.get("x-tebex-secret") != KEY:
            return httpx.Response(403, json={"error_message": "Invalid secret"})
        if request.url.path == "/information":
            return httpx.Response(
                200,
                json={
                    "account": {
                        "id": 1,
                        "domain": "https://survival-shop.tebex.io",
                        "name": "Survival Shop",
                        "currency": {"iso_4217": "USD", "symbol": "$"},
                        "online_mode": self.online_mode,
                        "game_type": "Minecraft (Java Edition)",
                        "log_events": False,
                    },
                    "server": {"id": 9, "name": "Survival"},
                },
            )
        if request.url.path == "/packages":
            return httpx.Response(200, json=self.packages)
        return httpx.Response(404)


def package(pid, name, price=4.99):
    return {"id": pid, "name": name, "price": price, "type": "single", "disabled": False}


@pytest.fixture
def tebex(monkeypatch):
    fake_api = FakeTebex()
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(fake_api))
    return fake_api


@pytest.fixture
def paper(tmp_path, monkeypatch, tebex):
    """Two Paper servers for a community, with Tebex and LuckPerms on the
    fake Modrinth."""
    for slug in ("tebex", "luckperms"):
        monkeypatch.setitem(PROJECTS, slug, {"builds": {NEW: "5.0"}, "loaders": ["paper"]})
    client = make_client(
        tmp_path, monkeypatch, kinds=("paper", "paper"), versions=(NEW, NEW), played_by="community"
    )
    # Paper makes plugins/bStats on its first start: the backup has something to hold.
    folder = ctx_of(client).config.mods_dir / "bStats"
    folder.mkdir(parents=True)
    (folder / "config.yml").write_text("enabled: true\n")
    yield client
    client.__exit__(None, None, None)


def tab(client, server="survival"):
    answer = client.get(f"/api/servers/{server}/store")
    assert answer.status_code == 200, answer.text
    return answer.json()


def connect(client, key=KEY, server="survival"):
    return client.post(f"/api/servers/{server}/store/connect", json={"key": key})


# ---------------------------------------------------------------- who gets a store
def test_only_a_community_paper_or_fabric_server_is_offered_a_store(paper):
    ctx = ctx_of(paper)
    assert tab(paper)["offered"] is True and tab(paper)["needs_type"] is False
    for played_by, words in (
        ("kids", "for kids never gets a store"),
        ("friends", "only for servers made for a community"),
        ("", "only for servers made for a community"),
    ):
        ctx.config.set("server.played_by", played_by)
        shown = tab(paper)
        assert shown["offered"] is False and words in shown["reason"]
        assert connect(paper).status_code == 400
    ctx.config.set("server.played_by", "community")
    ctx.config.set("server.test", True)
    assert "Test server never gets a store" in tab(paper)["reason"]
    ctx.config.set("server.test", False)
    ctx.config.set("server.type", "vanilla")
    shown = tab(paper)
    assert shown["offered"] is True and shown["needs_type"] is True
    answer = connect(paper)
    assert answer.status_code == 400 and "needs a Paper or Fabric server" in answer.text
    for kind in ("fabric", "purpur", "leaf"):
        ctx.config.set("server.type", kind)
        assert tab(paper)["needs_type"] is False
    # The apps read who it's for from the server's row.
    assert paper.get("/api/servers").json()["servers"][0]["played_by"] == "community"


def test_moderators_never_see_the_store_or_its_key(paper):
    owner = dict(paper.headers)
    helper = add_helper(paper, owner, servers=["survival"])
    for method, path, body in (
        ("GET", "/api/servers/survival/store", None),
        ("POST", "/api/servers/survival/store/connect", {"key": KEY}),
        ("POST", "/api/servers/survival/store/check", None),
    ):
        answer = paper.request(method, path, json=body, headers=helper)
        assert answer.status_code == 403, (path, answer.text)


# ---------------------------------------------------------------- connecting
def test_a_wrong_key_is_refused_and_nothing_is_installed(paper, tebex):
    assert "doesn't look like a Tebex secret key" in connect(paper, "short").text
    answer = connect(paper, "x" * 40)
    assert answer.status_code == 400 and "Tebex refused that key" in answer.text
    assert answer.status_code != 401  # 401 would sign the apps out
    assert tab(paper)["installed"] == {"tebex": False, "luckperms": False}
    assert store.key_of(ctx_of(paper)) is None


def test_connecting_installs_both_writes_the_key_and_makes_the_ranks(paper, tebex, caplog):
    caplog.set_level(logging.DEBUG)
    ctx = ctx_of(paper)
    answer = connect(paper)
    assert answer.status_code == 200, answer.text
    shown = answer.json()
    assert shown["undo"] is True and shown["outcome"]["undo"]["kind"] == "restore_backup"
    assert shown["outcome"]["added"] == ["tebex", "luckperms"]
    assert shown["installed"] == {"tebex": True, "luckperms": True} and shown["connected"]
    assert shown["store"]["link"] == "https://survival-shop.tebex.io"
    assert shown["buy_command"] == "/store"
    # Tebex's own config: the key, and its buy command named store.
    config = yaml.safe_load(store.tebex_config(ctx).read_text())
    assert config["server"]["secret-key"] == KEY
    assert config["buy-command"] == {"enabled": True, "name": "store"}
    # A new LuckPerms keeps its data in files, and the three ranks are there.
    assert store.storage(ctx) == "json"
    assert [r["made"] for r in shown["ranks"]] == [True, True, True]
    group = json.loads((store.luckperms_data(ctx) / "groups" / "supporter.json").read_text())
    assert group["prefixes"] == [{"prefix": "&a[Supporter] &f", "priority": 10}]
    assert group["permissions"] == [{"permission": "weight.10", "value": True}]
    # The key is kept encrypted, read only by the agent, and never answered or logged.
    assert store.key_of(ctx) == KEY
    assert KEY not in json.dumps(shown) and KEY not in json.dumps(tab(paper))
    assert KEY not in caplog.text
    audit = ctx.core.db.query("SELECT * FROM audit_log WHERE action = 'store_connect'")
    assert audit and KEY not in json.dumps([dict(r) for r in audit])
    # Only Tebex's two read calls, with the key in its header.
    assert {(m, p) for m, p, _ in tebex.calls} == {("GET", "/information")}


def test_connecting_again_keeps_the_owners_own_ranks_and_luckperms(paper):
    ctx = ctx_of(paper)
    assert connect(paper).status_code == 200
    mine = store.luckperms_data(ctx) / "groups" / "patron.json"
    mine.write_text('{"name": "patron", "prefixes": [{"prefix": "&dMy own", "priority": 1}]}')
    assert connect(paper).status_code == 200
    assert "My own" in mine.read_text()


def test_an_existing_luckperms_is_never_switched_and_ranks_are_made_by_hand(paper):
    ctx = ctx_of(paper)
    config = store.luckperms_config(ctx)
    config.parent.mkdir(parents=True)
    config.write_text("storage-method: h2\n")
    shown = connect(paper).json()
    assert config.read_text() == "storage-method: h2\n"
    assert shown["ranks_by_hand"] is True and not any(r["made"] for r in shown["ranks"])
    assert "lp user {id} parent add supporter" in shown["checklist"]


def test_geyser_needs_an_offline_store(paper, tebex):
    ctx = ctx_of(paper)
    ctx.config.set("server.crossplay", True)
    answer = connect(paper)
    assert answer.status_code == 400 and "Minecraft: Offline" in answer.text
    assert store.key_of(ctx) is None
    tebex.online_mode = False
    shown = connect(paper).json()
    assert shown["store"]["offline"] is True and "usernames" in shown["offline_warning"]
    assert "lp user {username} parent add legend" in shown["checklist"]
    assert "{id}" not in shown["checklist"]


# ---------------------------------------------------------------- the checklist
def test_the_checklist_has_every_step_and_no_price(paper):
    words = tab(paper)["checklist"]
    for needed in (
        "18 or older",
        "Minecraft: Java Edition",
        'Package "Supporter"',
        "When bought (Initial): lp user {id} parent add supporter",
        "On expiry: lp user {id} parent remove supporter",
        "On refund: lp user {id} parent remove patron",
        "On chargeback: lp user {id} parent remove legend",
        "without a / at the start",
        "gifting on",
        "Capes",
        "loot boxes",
        "does it change gameplay?",
        "manual payment",
        "5%",
        "NOT AN OFFICIAL MINECRAFT PRODUCT",
        "(your email or Discord name)",
    ):
        assert needed in words, needed
    assert "$" not in words and "/lp" not in words


# ---------------------------------------------------------------- Check my store
def test_check_my_store_ticks_the_ranks_and_reminds_once_about_the_rest(paper, tebex):
    assert "Connect the store first" in paper.post("/api/servers/survival/store/check").text
    connect(paper)
    tebex.packages = [package(1, "Supporter"), package(2, "legend", 9.5), package(3, "Diamond kit")]
    checked = paper.post("/api/servers/survival/store/check").json()
    assert [(p["name"], p["rank"]) for p in checked["packages"]] == [
        ("Supporter", "supporter"),
        ("legend", "legend"),
        ("Diamond kit", None),
    ]
    assert checked["packages"][1]["price"] == 9.5
    assert checked["remind"] == ["Diamond kit"]
    assert paper.post("/api/servers/survival/store/check").json()["remind"] == []
    assert tab(paper)["packages"][0]["name"] == "Supporter"


# ---------------------------------------------------------------- who holds a rank
def write_user(ctx, uuid, parents):
    path = store.luckperms_data(ctx) / "users" / f"{uuid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"uuid": uuid, "name": "x", "parents": parents}))


def test_holders_count_only_live_global_store_ranks(paper):
    ctx = ctx_of(paper)
    connect(paper)
    write_user(ctx, ALEX, [{"group": "default"}, {"group": "patron"}])
    write_user(ctx, "u-2", [{"group": "legend", "expiry": time.time() - 5}])
    write_user(ctx, "u-3", [{"group": "legend", "context": {"world": "nether"}}])
    write_user(ctx, "u-4", [{"group": "supporter", "expiry": time.time() + 3600}])
    assert store.holders(ctx) == {ALEX: {"patron"}, "u-4": {"supporter"}}


# ---------------------------------------------------------------- Discord
def store_on(core):
    """The fake Discord's one server, set up as a community store."""
    ctx = core.servers["test"]
    ctx.config.set("server.played_by", "community")
    store._save_key(ctx, KEY)
    store._remember(ctx, link="https://survival-shop.tebex.io")
    return ctx


def test_store_in_discord_posts_the_link(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    assert run(fake, "store")[0] == "Test Server has no store."
    store_on(core)
    words = run(fake, "store")[0]
    assert "https://survival-shop.tebex.io" in words and "/store" in words


def test_store_rank_roles_follow_the_rank(agent, fake):  # noqa: F811
    set_up(agent)
    core = core_of(agent)
    ctx = store_on(core)
    config = store.luckperms_config(ctx)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("storage-method: json\n")
    linked(core, "Alex", ALEX)
    path = f"/api/discord/guilds/{GUILD_ID}/levels"
    shown = agent.get(path).json()
    assert [r["rank"] for r in shown["store_ranks"]] == ["supporter", "patron", "legend"]
    # A role with powers in Discord is never a Store rank role.
    bot_role = layout.our_roles(core.db, GUILD_ID)["mod"]
    body = {"announce": True, "leaderboard_day": None}
    refused = agent.put(
        path, json={**body, "store_roles": [{"rank": "patron", "role_id": bot_role}]}
    )
    assert refused.status_code == 400
    saved = agent.put(path, json={**body, "store_roles": [{"rank": "patron", "role_id": ROLE}]})
    assert saved.status_code == 200, saved.text
    write_user(ctx, ALEX, [{"group": "patron"}])
    agent.portal.call(core.discord.update_store_roles)
    assert ROLE in fake.members[MEMBER_ID]
    # Nothing changed: Discord isn't asked again, and not before the hour.
    calls = len(fake.calls)
    core.discord.resync_store()
    agent.portal.call(core.discord.update_store_roles)
    assert len([c for c in fake.calls[calls:] if "/members/" in str(c)]) == 0
    # Refunded: the rank is gone, and so is the role (after a player joins).
    write_user(ctx, ALEX, [])
    core.discord.resync_store()
    agent.portal.call(core.discord.update_store_roles)
    assert ROLE not in fake.members[MEMBER_ID]
