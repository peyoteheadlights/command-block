"""Wake-on-LAN, the agent's half: the cable check, the
recorded MAC, "Try it", the helping PC and one wake key per device."""

import pytest

from agent import wake

from .test_devices import sign_in
from .test_helpers import add_helper

CABLE = {
    "adapter": "Ethernet",
    "mac": "aa:bb:cc:dd:ee:ff",
    "address": "192.168.1.20",
    "broadcast": "192.168.1.255",
    "wired": True,
}


@pytest.fixture
def adapter(monkeypatch):
    now = dict(CABLE)
    monkeypatch.setattr(wake, "read_adapter", lambda: dict(now) if now else None)
    return now


@pytest.fixture
def owner(multi_client):
    return dict(multi_client.headers)


def turn_on(client):
    response = client.put("/api/wake/setup", json={"on": True})
    assert response.status_code == 200, response.text
    return response.json()


def test_setting_up_records_the_cable_adapter(multi_client, adapter):
    state = turn_on(multi_client)
    assert state["on"] is True and state["mac"] == "aa:bb:cc:dd:ee:ff"
    assert state["now"]["wired"] is True


def test_wifi_stops_the_setup_with_a_plain_message(multi_client, adapter):
    adapter["wired"] = False
    response = multi_client.put("/api/wake/setup", json={"on": True})
    assert response.status_code == 400
    assert "network cable" in response.json()["detail"]
    assert multi_client.get("/api/wake/setup").json()["on"] is False


def test_a_new_network_card_is_picked_up_while_it_runs(multi_client, adapter):
    turn_on(multi_client)
    adapter["mac"] = "11:22:33:44:55:66"
    assert multi_client.get("/api/wake/setup").json()["mac"] == "11:22:33:44:55:66"


def test_try_it_marks_tested_when_the_pc_wakes(multi_client, adapter):
    turn_on(multi_client)
    assert multi_client.post("/api/wake/try").json()["trying_since"]
    core = multi_client.app.state.core
    assert not core.wake.noticed(1000.0, 1010.5)  # an ordinary step
    assert core.wake.noticed(1000.0, 4600.0)  # an hour went by in one step: it slept
    state = multi_client.get("/api/wake/setup").json()
    assert state["tested_at"] == 4600.0 and state["trying_since"] is None


def test_the_router_page_must_be_https(multi_client, adapter):
    bad = multi_client.put("/api/wake/setup", json={"router_url": "file:///C:/x.exe"})
    assert bad.status_code == 400
    good = multi_client.put("/api/wake/setup", json={"router_url": "https://abc.myfritz.net"})
    assert good.json()["router_url"] == "https://abc.myfritz.net"


def test_each_device_gets_its_own_key_and_the_helper_lists_their_hashes(multi_client, adapter):
    turn_on(multi_client)
    _found, helper_pc = sign_in(multi_client, label="Desktop app on HELPER")
    made = multi_client.put(
        "/api/wake/helper", json={"address": "100.64.1.2", "port": 47391}, headers=helper_pc
    )
    assert made.status_code == 200, made.text
    assert made.json()["helper"]["label"] == "Desktop app on HELPER"
    info = multi_client.get("/api/wake").json()
    assert info["helper"] == {"address": "100.64.1.2", "port": 47391}
    assert info["mac"] == "aa:bb:cc:dd:ee:ff" and info["has_key"] is False
    key = multi_client.post("/api/wake/key").json()["key"]
    assert multi_client.get("/api/wake").json()["has_key"] is True
    listed = multi_client.get("/api/wake/keys", headers=helper_pc).json()
    assert listed["keys"] == [wake.key_hash(key)]
    assert listed["mac"] == "aa:bb:cc:dd:ee:ff"
    # Only the helping PC gets the list.
    assert multi_client.get("/api/wake/keys").status_code == 403


def test_signing_a_device_out_drops_its_key(multi_client, adapter):
    turn_on(multi_client)
    _found, helper_pc = sign_in(multi_client)
    multi_client.put(
        "/api/wake/helper", json={"address": "100.64.1.2", "port": 47391}, headers=helper_pc
    )
    _found, phone = sign_in(multi_client)
    multi_client.post("/api/wake/key", headers=phone)
    assert len(multi_client.get("/api/wake/keys", headers=helper_pc).json()["keys"]) == 1
    multi_client.post("/api/auth/logout", headers=phone)
    assert multi_client.get("/api/wake/keys", headers=helper_pc).json()["keys"] == []


def test_the_helping_pc_still_helps_after_a_new_sign_in_key(multi_client, adapter):
    turn_on(multi_client)
    _found, helper_pc = sign_in(multi_client)
    multi_client.put(
        "/api/wake/helper", json={"address": "100.64.1.2", "port": 47391}, headers=helper_pc
    )
    rotated = multi_client.post("/api/auth/rotate", headers=helper_pc)
    assert rotated.status_code == 200, rotated.text
    helper_pc = {"Authorization": f"Bearer {rotated.json()['token']}"}
    assert multi_client.get("/api/wake/keys", headers=helper_pc).status_code == 200
    assert multi_client.get("/api/wake/setup").json()["helper"] is not None


def test_a_moderator_wakes_only_with_start_the_agent(multi_client, owner, adapter):
    turn_on(multi_client)
    _found, helper_pc = sign_in(multi_client)
    multi_client.put(
        "/api/wake/helper", json={"address": "100.64.1.2", "port": 47391}, headers=helper_pc
    )
    sam = add_helper(multi_client, owner)
    assert multi_client.get("/api/wake", headers=sam).status_code == 403
    multi_client.put("/api/accounts/sam", json={"pc": ["agent.start"]}, headers=owner)
    assert multi_client.get("/api/wake", headers=sam).status_code == 200
    multi_client.post("/api/wake/key", headers=sam)
    assert len(multi_client.get("/api/wake/keys", headers=helper_pc).json()["keys"]) == 1
    # Taking the permission away drops the key at the helping PC's next sync.
    multi_client.put("/api/accounts/sam", json={"pc": []}, headers=owner)
    assert multi_client.get("/api/wake/keys", headers=helper_pc).json()["keys"] == []


def test_the_helping_pc_must_be_on_tailscale(multi_client, adapter):
    response = multi_client.put("/api/wake/helper", json={"address": "192.168.1.5", "port": 47391})
    assert response.status_code == 400


def test_turning_it_off_forgets_the_keys(multi_client, adapter):
    turn_on(multi_client)
    multi_client.post("/api/wake/key")
    multi_client.put("/api/wake/setup", json={"on": False})
    info = multi_client.get("/api/wake").json()
    assert info["on"] is False and info["has_key"] is False


def test_the_adapter_is_read_from_the_home_address(monkeypatch):
    import socket
    from types import SimpleNamespace

    def snicaddr(family, address, netmask, *_):
        return SimpleNamespace(family=family, address=address, netmask=netmask)

    monkeypatch.setattr(wake, "home_address", lambda: "192.168.1.20")
    monkeypatch.setattr(wake, "adapter_kinds", lambda: {"aa:bb:cc:dd:ee:ff": wake.CABLE})
    monkeypatch.setattr(
        wake.psutil,
        "net_if_addrs",
        lambda: {
            "Ethernet": [
                snicaddr(wake.psutil.AF_LINK, "AA-BB-CC-DD-EE-FF", None, None, None),
                snicaddr(socket.AF_INET, "192.168.1.20", "255.255.255.0", None, None),
            ]
        },
    )
    assert wake.read_adapter() == CABLE
    monkeypatch.setattr(wake, "adapter_kinds", lambda: {})
    assert wake.read_adapter()["wired"] is None  # Windows didn't say: not guessed
