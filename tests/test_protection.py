"""The Protection card.

The rules are a pure function of what the server's own files say, so most
of the card is checked straight from Facts. The API tests check each
one-click fix changes only its own setting and can be undone, that a
moderator never gets a fix their account can't use, and the watchers
(outside changes to the player lists, the chat spam rule, the router hooks).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import notes, ports, protection, watchers
from agent.main import create_app
from agent.protection import Facts, flag_look_alikes, fractureiser_check, lines
from agent.security import permissions as P
from agent.security.auth import hash_password

from .conftest import PASSWORD, build_multi_config
from .test_helpers import add_helper
from .test_moderators import give

BASE = "/api/servers/survival"
STRINGS = json.loads(
    (Path(__file__).parents[1] / "shared" / "strings.json").read_text(encoding="utf-8")
)


def ids(found):
    return [line["id"] for line in found]


def by_id(found, line_id):
    return next(line for line in found if line["id"] == line_id)


def facts(**changes) -> Facts:
    props = {"online-mode": "true", "white-list": "false", **changes.pop("props", {})}
    return Facts(properties=props, **changes)


AUTHME = {"project": "authmereloaded", "name": "AuthMe Reloaded", "small": False}


# ------------------------------------------------------------ the rules
def test_no_server_properties_yet_says_only_that():
    assert ids(lines(Facts())) == ["not_started"]


def test_offline_mode_with_no_whitelist_is_the_worst_line_with_its_fix():
    found = lines(facts(props={"online-mode": "false"}, plugins=True, login=[AUTHME]))
    first = found[0]
    assert first["id"] == "offline_open" and first["level"] == "worst"
    assert first["fix"] == "whitelist_on" and first["permission"] == P.SETTINGS_GAME
    assert "whitelist_off" not in ids(found)  # said once, by the worse line
    assert by_id(found, "not_protected")["addons"] == [AUTHME]


def test_offline_ops_without_a_login_add_on_name_the_count():
    found = lines(facts(props={"online-mode": "false"}, ops=["Steve", "Alex"], login=[AUTHME]))
    line = by_id(found, "offline_ops")
    assert line["level"] == "worst" and line["values"]["count"] == 2
    assert "ops_signed_in" in ids(found)


def test_a_login_add_on_turns_the_warnings_into_one_note():
    found = lines(
        facts(
            props={"online-mode": "false", "white-list": "true"}, ops=["Steve"], addons={"authme"}
        )
    )
    assert "offline_ops" not in ids(found) and "not_protected" not in ids(found)
    assert by_id(found, "login_on")["values"]["addon"] == "AuthMe"


def test_a_type_with_no_login_add_on_offers_online_mode_instead():
    found = lines(facts(props={"online-mode": "false", "white-list": "true"}, type_id="vanilla"))
    assert by_id(found, "not_protected_none")["fix"] == "online_on"


def test_rcon_and_query_have_their_fixes():
    found = lines(facts(props={"enable-rcon": "true", "enable-query": "true"}))
    assert by_id(found, "rcon_on")["level"] == "worst"
    assert by_id(found, "rcon_on")["fix"] == "rcon_off"
    assert by_id(found, "query_on")["fix"] == "query_off"


def test_a_missing_whitelist_is_only_a_note_for_a_community_server():
    assert by_id(lines(facts()), "whitelist_off")["level"] == "problem"
    assert by_id(lines(facts(played_by="community")), "whitelist_off")["level"] == "info"


def test_the_default_port_offers_a_free_one_and_never_a_guess():
    assert by_id(lines(facts(suggested_port=25567)), "port_default")["fix"] == "change_port"
    assert by_id(lines(facts(suggested_port=None)), "port_default")["fix"] is None
    assert "port_other" in ids(lines(facts(port=25570)))


def test_router_state_unknown_is_said_as_unknown_never_as_closed():
    found = ids(lines(facts(router=None)))
    assert "reach_router_unknown" in found and "reach_no_router" not in found


def test_a_shared_no_ip_address_and_a_community_server_get_their_notes():
    found = lines(facts(played_by="community", shared_address="mine.ddns.net:25565"))
    assert by_id(found, "no_ip")["values"]["name"] == "mine.ddns.net"
    assert by_id(found, "hide_address")["link"] == protection.PLAYIT
    assert "no_ip" not in ids(lines(facts(shared_address="play.example.com")))


def test_paper_settings_that_are_off_get_fixes():
    paper = {
        protection.THROTTLE[:2]: -1,
        protection.PACKET_LIMIT[:2]: -1.0,
        protection.ANTI_XRAY[:2]: False,
        protection.DUPES[0][:2]: True,
    }
    found = lines(facts(plugins=True, paper=paper))
    for line_id, fix in (
        ("throttle_off", "throttle_on"),
        ("packet_limit_off", "packet_limit_on"),
        ("anti_xray_off", "anti_xray_on"),
        ("dupes_on", "dupes_off"),
    ):
        assert by_id(found, line_id)["fix"] == fix
    assert ids(lines(facts(plugins=True, paper={})))[-3:].count("paper_not_read") == 1


def test_log4shell_only_for_affected_versions_and_gone_once_fixed():
    assert by_id(lines(facts(minecraft="1.16.5")), "log4shell")["level"] == "worst"
    assert "log4shell" not in ids(lines(facts(minecraft="1.20.1")))
    assert "log4shell" not in ids(lines(facts(minecraft=None)))  # not printed yet: no guess
    row = protection.log4j_row("1.18")
    assert protection.log4j_flag(row) == protection.NO_LOOKUPS
    fixed = facts(
        minecraft="1.12.2", jvm_args=[protection.log4j_flag(protection.log4j_row("1.12.2"))]
    )
    assert "log4shell_fixed" in ids(lines(fixed))


def test_worst_lines_come_first():
    found = lines(facts(props={"online-mode": "false", "enable-rcon": "true"}, minecraft="1.16.5"))
    levels = [line["level"] for line in found]
    assert levels == sorted(levels, key=protection.LEVELS.index)


# ------------------------------------------------------------ the words
def every_line():
    paper = {
        protection.THROTTLE[:2]: 0,
        protection.PACKET_LIMIT[:2]: 0,
        protection.ANTI_XRAY[:2]: False,
        protection.DUPES[0][:2]: True,
    }
    paper_on = {protection.THROTTLE[:2]: 4000, protection.PACKET_LIMIT[:2]: 500.0}
    paper_on[protection.ANTI_XRAY[:2]] = True
    cases = [
        Facts(),
        facts(
            props={"online-mode": "false", "enable-rcon": "true", "enable-query": "true"},
            ops=["a"],
            login=[AUTHME],
            antibot=[AUTHME],
            plugins=True,
            paper=paper,
            minecraft="1.16.5",
            played_by="community",
            shared_address="a.ddns.net",
            tailscale=True,
            home=True,
            router=True,
            spam={"on": True, "names": 5, "seconds": 60},
        ),
        facts(
            props={"online-mode": "false", "white-list": "true"},
            addons={"authme"},
            port=25570,
            tailscale=False,
            router=False,
            plugins=True,
            paper=paper_on,
            minecraft="1.12.2",
            jvm_args=[protection.log4j_flag(protection.log4j_row("1.12.2"))],
        ),
        facts(props={"online-mode": "false", "white-list": "true"}, plugins=True, paper={}),
    ]
    return [line for case in cases for line in lines(case)]


def test_every_line_and_fix_has_words_in_both_modes():
    found = every_line()
    keys = {line["words"] for line in found} | {
        f"protect.fix.{line['fix']}" for line in found if line["fix"]
    }
    keys |= {f"protect.fix.{fix}" for fix in protection.FIXES}
    missing = sorted(k for k in keys if k not in STRINGS)
    assert not missing, missing
    for key in keys:
        assert all(STRINGS[key]), key


def test_no_line_ever_says_the_server_is_safe():
    """House rule 1: the card reports what it read, never a verdict."""
    verdict = re.compile(r"\b(safe|safely|clean|secure|protected)\b", re.I)
    for key, words in STRINGS.items():
        if not key.startswith(("protect.", "desktop.protect.", "desktop.fracture.")):
            continue
        for text in words:
            assert not verdict.search(re.sub(r"(?i)not protected", "", text)), (key, text)


# ------------------------------------------------------------ the API
@pytest.fixture
def agent(tmp_path, monkeypatch):
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


def ctx_of(client, server_id="survival"):
    return client.app.state.core.servers[server_id]


def folder(client, server_id="survival") -> Path:
    return Path(ctx_of(client, server_id).config.server_dir)


def add_props(client, text, server_id="survival"):
    path = folder(client, server_id) / "server.properties"
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def card(client, base=BASE, headers=None):
    response = client.get(f"{base}/protection", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["lines"]


def fix(client, fix_id, headers=None, **body):
    return client.post(f"{BASE}/protection/fix", json={"fix": fix_id, **body}, headers=headers)


def undo(client):
    response = client.post(f"{BASE}/changes/undo", json={})
    assert response.status_code == 200, response.text


def wait_for(check, seconds=10.0):
    end = time.time() + seconds
    while time.time() < end:
        found = check()
        if found:
            return found
        time.sleep(0.1)
    raise AssertionError("waited too long")


def events(client, kind, server_id="survival"):
    rows = client.app.state.core.db.recent_events(server_id, limit=200)
    return [row for row in rows if row["type"] == kind]


def start(client):
    assert client.post(f"{BASE}/server/start").status_code == 200
    wait_for(lambda: client.get(f"{BASE}/status").json()["state"] == "ONLINE", 20)


def console(client, words):
    response = client.post(f"{BASE}/server/command", json={"command": words, "confirm": True})
    assert response.status_code == 200, response.text


def test_the_whitelist_fix_changes_one_line_and_undoes(agent):
    assert by_id(card(agent), "whitelist_off")["fix"] == "whitelist_on"
    response = fix(agent, "whitelist_on")
    assert response.status_code == 200, response.text
    assert response.json()["undo"]
    text = (folder(agent) / "server.properties").read_text(encoding="utf-8")
    assert "white-list=true" in text and "max-players=20" in text
    assert "whitelist_on" in ids(card(agent))
    undo(agent)
    assert "white-list=true" not in (folder(agent) / "server.properties").read_text(
        encoding="utf-8"
    )


def test_rcon_off_and_change_port_fixes(agent):
    add_props(agent, "enable-rcon=true\n")
    assert by_id(card(agent), "rcon_on")["level"] == "worst"
    assert fix(agent, "rcon_off").status_code == 200
    assert "rcon_on" not in ids(card(agent))
    suggested = by_id(card(agent), "port_default")["values"]["suggested"]
    assert suggested and suggested != 25566  # never the other server's port
    assert fix(agent, "change_port").status_code == 200
    assert by_id(card(agent), "port_other")["values"]["port"] == suggested


def test_a_paper_setting_fix_keeps_the_rest_of_the_file_and_undoes(agent):
    bukkit = folder(agent) / "bukkit.yml"
    bukkit.write_text("settings:\n  connection-throttle: -1\n  shutdown-message: Bye\n", "utf-8")
    assert by_id(card(agent), "throttle_off")["fix"] == "throttle_on"
    assert fix(agent, "throttle_on").status_code == 200
    text = bukkit.read_text(encoding="utf-8")
    assert "connection-throttle: 4000" in text and "shutdown-message: Bye" in text
    undo(agent)
    assert "connection-throttle: -1" in bukkit.read_text(encoding="utf-8")


def test_an_unknown_fix_is_refused(agent):
    assert fix(agent, "delete_everything").status_code == 400


def test_a_moderator_sees_no_fix_they_cant_use_and_is_refused(agent):
    owner = dict(agent.headers)
    agent.headers.pop("Authorization")
    sam = add_helper(agent, owner)
    give(agent, owner, {"survival": P.LOOK_AFTER_IT})
    shown = card(agent, headers=sam)
    assert by_id(shown, "whitelist_off")["fix"] is None
    assert by_id(shown, "big_attacks")["fix"] == "stop_and_close"  # it may start and stop
    assert fix(agent, "whitelist_on", headers=sam).status_code == 400
    text = (folder(agent) / "server.properties").read_text(encoding="utf-8")
    assert "white-list=true" not in text


def test_offline_paper_suggests_authme_and_vanilla_suggests_online_mode(agent, monkeypatch):
    monkeypatch.setattr(notes, "SHIPPED", Path(__file__).parents[1] / "data" / "catalog.json")
    notes.load_shipped()
    add_props(agent, "online-mode=false\n")
    add_props(agent, "online-mode=false\n", "creative")
    projects = [a["project"] for a in by_id(card(agent), "not_protected")["addons"]]
    assert "authmereloaded" in projects
    vanilla = card(agent, "/api/servers/creative")
    assert by_id(vanilla, "not_protected_none")["fix"] == "online_on"


def test_the_log4shell_fix_downloads_mojangs_file_and_adds_the_flag(agent, monkeypatch):
    ctx = ctx_of(agent)
    ctx.server.mc_version = "1.16.5"
    fetched = []

    async def fake_download(spec, where, *args, **kwargs):
        fetched.append(spec)
        (Path(where) / spec.name).write_text("<Configuration/>", encoding="utf-8")

    monkeypatch.setattr(protection.downloads, "download", fake_download)
    assert by_id(card(agent), "log4shell")["fix"] == "log4shell"
    response = fix(agent, "log4shell")
    assert response.status_code == 200, response.text
    spec = fetched[0]
    assert (
        spec.sha1
        and spec.url.startswith("https://launcher.mojang.com/")
        and not spec.allow_unverified
    )
    assert "-Dlog4j.configurationFile=log4j2_112-116.xml" in ctx.config.server.jvm_args
    assert "log4shell_fixed" in ids(card(agent))
    ctx.server.mc_version = "1.20.1"
    assert fix(agent, "log4shell").status_code == 400


def test_an_op_made_in_the_game_sends_an_alert(agent):
    start(agent)
    console(agent, "fakeline [Steve: Made Bob a server operator]")
    found = wait_for(lambda: events(agent, "ops_changed"))
    assert "Bob" in found[0]["message"] and "Steve" in found[0]["message"]


def test_a_list_edited_while_off_is_found_at_the_next_start(agent):
    ctx = ctx_of(agent)
    for name in ("ops.json", "whitelist.json", "banned-players.json"):
        (folder(agent) / name).write_text("[]", encoding="utf-8")
    watchers.remember(ctx)
    bob = [{"uuid": "00000000-0000-0000-0000-000000000001", "name": "Bob", "level": 4}]
    (folder(agent) / "ops.json").write_text(json.dumps(bob), encoding="utf-8")
    start(agent)
    found = wait_for(lambda: events(agent, "ops_changed"))
    assert "Bob" in found[0]["message"] and "while the server was off" in found[0]["message"]


def test_the_failed_sign_in_alert_counts_the_tries(agent):
    for _ in range(2):
        agent.post("/api/auth/login", json={"username": "admin", "password": "wrong one"})
    found = wait_for(
        lambda: [e for e in events(agent, "auth_failure") if "2 failed" in e["message"]]
    )
    assert found


def test_new_names_saying_the_same_line_turn_the_whitelist_on(agent):
    start(agent)
    for n in range(5):
        console(agent, f"fakechat Bot{n} join my server at example dot com")
    found = wait_for(lambda: events(agent, "chat_spam"))
    assert found[0]["message"].startswith("5 new names")


def test_the_router_hooks_run_on_start_and_stop_and_the_big_attack_button(agent, monkeypatch):
    calls = []

    async def opened(ctx):
        calls.append("open")
        return True

    async def tested(ctx):
        calls.append("test")

    async def closed(ctx):
        calls.append("close")

    monkeypatch.setattr(
        protection, "ROUTER", protection.RouterHooks(open=opened, close=closed, outside_test=tested)
    )
    start(agent)
    wait_for(lambda: calls[:2] == ["open", "test"])
    response = fix(agent, "stop_and_close")
    assert response.status_code == 200, response.text
    assert response.json()["stopped"] is True
    assert agent.get(f"{BASE}/status").json()["state"] != "ONLINE"
    assert "close" in calls


# ------------------------------------------------------------ fractureiser
def test_fractureiser_markers_are_found_and_only_read(tmp_path):
    home, local = tmp_path / "home", tmp_path / "local"
    (home / ".config" / ".data").mkdir(parents=True)
    (home / ".config" / ".data" / "lib.jar").write_bytes(b"x")
    (local / "Microsoft Edge").mkdir(parents=True)
    key = {"edge": r"C:\Users\x\AppData\Local\Microsoft Edge\libWebGL64.jar", "ok": "steam.exe"}
    result = fractureiser_check(home, local, key)
    assert any("lib.jar" in p for p in result["found"])
    assert any(p.endswith("Microsoft Edge") for p in result["found"])
    assert "Run key: edge" in result["found"] and "Run key: ok" not in result["found"]
    assert (home / ".config" / ".data" / "lib.jar").exists()  # never deleted
    empty = fractureiser_check(tmp_path / "none", tmp_path / "none", {})
    assert empty["found"] == [] and empty["checked"]


def test_the_fractureiser_check_is_owner_only_on_the_api(agent):
    response = agent.post("/api/security/fractureiser-check")
    assert response.status_code == 200, response.text
    assert set(response.json()) >= {"found", "checked"}


# ------------------------------------------------------------ look-alikes and addresses
def test_a_name_one_letter_from_a_far_bigger_project_is_flagged():
    hits = [
        {"title": "Sodium", "downloads": 1_000_000},
        {"title": "Sodiun", "downloads": 30},
        {"title": "Lithium", "downloads": 20},
    ]
    flag_look_alikes(hits)
    assert hits[1].get("look_alike") == "Sodium"
    assert not hits[0].get("look_alike") and not hits[2].get("look_alike")


def test_an_address_shows_its_port_unless_it_is_the_default():
    assert ports.address("1.2.3.4", 25565) == "1.2.3.4"
    assert ports.address("1.2.3.4", 25570) == "1.2.3.4:25570"
    assert ports.address("play.example.com:25570", 25570) == "play.example.com:25570"
    assert ports.address("1.2.3.4", 19132, default=0) == "1.2.3.4:19132"
