"""Wake-on-LAN in the desktop app: the PC settings card,
the helping PC, and Wake on the "agent not running" screen."""

from __future__ import annotations

import hashlib
import socket
import time

import pytest

from agent import API_VERSION
from agent import wake as agent_wake
from desktop import wake
from desktop.agent import AgentClient

from .fake_pc import FakePC
from .test_desktop_app import button, owner, shown_text, sign_in, wait_for
from .test_desktop_gear import shown_buttons, shows

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")

CABLE = {
    "adapter": "Ethernet",
    "mac": "aa:bb:cc:dd:ee:ff",
    "address": "192.168.1.20",
    "broadcast": "192.168.1.255",
    "wired": True,
}


@pytest.fixture
def cable(monkeypatch):
    monkeypatch.setattr(agent_wake, "read_adapter", lambda: dict(CABLE))


def window_on(qapp, agent_http, pc, place="this PC"):
    from desktop.window import MainWindow

    window = MainWindow(AgentClient(agent_http, place=place), pc, dark=False)
    window.show()
    sign_in(qapp, window)
    wait_for(qapp, lambda: window.role == "owner")
    return window


def pc_settings(qapp, window):
    window.set_mode(True)
    window.open_list("PC settings", "pc")
    return window.gear.sections["pc"]


def setup_wake(agent_http):
    response = agent_http.put("/api/wake/setup", json={"on": True}, headers=owner(agent_http))
    assert response.status_code == 200, response.text


# ------------------------------------------------------------ the helping PC's listener
def helper_with_key(sent):
    helper = wake.Helper(lambda mac, broadcast: sent.append((mac, broadcast)))
    helper.update(
        {
            "mac": "aa:bb:cc:dd:ee:ff",
            "broadcast": "192.168.1.255",
            "keys": [agent_wake.key_hash("k1")],
        }
    )
    return helper


def test_the_helper_wakes_only_the_recorded_pc_for_a_valid_key():
    sent: list = []
    helper = helper_with_key(sent)
    assert not helper.request(wake.PREFIX + b"wrong")
    assert not helper.request(b"k1")  # not a wake request at all
    assert helper.request(wake.PREFIX + b"k1")
    assert sent == [("aa:bb:cc:dd:ee:ff", "192.168.1.255")]
    # At most one wake every 30 seconds.
    assert not helper.request(wake.PREFIX + b"k1")
    helper.last -= wake.EVERY
    assert helper.request(wake.PREFIX + b"k1")


def test_a_dropped_key_stops_working_at_the_next_sync():
    sent: list = []
    helper = helper_with_key(sent)
    helper.update({"mac": "aa:bb:cc:dd:ee:ff", "broadcast": None, "keys": []})
    assert not helper.request(wake.PREFIX + b"k1")


def test_the_helper_listens_and_answers_a_real_request():
    sent: list = []
    helper = helper_with_key(sent)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    helper.listen("127.0.0.1", port)
    try:
        wake.ask_helper("127.0.0.1", port, "k1")
        deadline = time.monotonic() + 5
        while not sent and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        helper.stop()
    assert sent == [("aa:bb:cc:dd:ee:ff", "192.168.1.255")]


def test_the_wake_signal_is_the_mac_sixteen_times():
    packet = wake.magic_packet("aa:bb:cc:dd:ee:ff")
    assert packet[:6] == b"\xff" * 6 and packet[6:] == bytes.fromhex("aabbccddeeff") * 16


# ------------------------------------------------------------ PC settings
def test_wake_on_lan_is_only_in_technical(qapp, agent_http, cable):
    window = window_on(qapp, agent_http, FakePC())
    window.open_list("PC settings", "pc")
    page = window.gear.sections["pc"]
    shows(qapp, page, "Save alerts")
    assert "Set up Wake-on-LAN" not in shown_buttons(page)
    page = pc_settings(qapp, window)
    shows(qapp, page, "Turn on Wake-on-LAN")
    assert "Cable: Ethernet, MAC aa:bb:cc:dd:ee:ff" in shown_text(page)
    button(page, "Turn on Wake-on-LAN").click()
    shows(qapp, page, "Start test")
    assert "Command Block never changes Windows settings." in shown_text(page)
    button(page, "Start test").click()
    wait_for(
        qapp,
        lambda: agent_http.get("/api/wake/setup", headers=owner(agent_http)).json()["trying_since"],
    )


def test_another_pc_helps_wake_and_keeps_the_keys(qapp, agent_http, cable):
    setup_wake(agent_http)
    pc = FakePC()
    window = window_on(qapp, agent_http, pc, place="100.64.0.1")
    page = pc_settings(qapp, window)
    words = "Listen for wake requests for " + socket.gethostname()
    wait_for(qapp, lambda: any(b.text() == words for b in page.findChildren(QtWidgets.QCheckBox)))
    next(b for b in page.findChildren(QtWidgets.QCheckBox) if b.text() == words).click()
    wait_for(qapp, lambda: pc.helping == "100.64.0.9")
    state = agent_http.get("/api/wake/setup", headers=owner(agent_http)).json()
    assert state["helper"]["address"] == "100.64.0.9"
    # This device kept its own key, which the helping PC (itself here) accepts.
    window.load_wake()
    wait_for(qapp, lambda: window.wake_kept().get("key"))
    key = window.wake_kept()["key"]
    window.sync_helper()
    # An earlier list (from before the key was made) may come back first.
    wait_for(qapp, lambda: hashlib.sha256(key.encode()).hexdigest() in pc.helper.keys)
    assert pc.helper.request(wake.PREFIX + key.encode())
    assert pc.wakes == [("aa:bb:cc:dd:ee:ff", "192.168.1.255")]


# ------------------------------------------------------------ pressing Wake
def test_wake_on_the_agent_not_running_screen(qapp, agent_http, cable):
    setup_wake(agent_http)
    agent_http.put(
        "/api/wake/setup", json={"router_url": "https://abc.myfritz.net"}, headers=owner(agent_http)
    )
    pc = FakePC()
    window = window_on(qapp, agent_http, pc, place="100.64.0.1")
    wait_for(qapp, lambda: window.wake_kept().get("on"))
    window.agent_down()
    name = socket.gethostname()
    assert button(window, f"Wake {name}")
    button(window, f"Wake {name}").click()
    # Only a router: its wake page opens, and the signal goes out from here too.
    assert pc.opened == ["https://abc.myfritz.net"]
    wait_for(qapp, lambda: pc.wakes == [("aa:bb:cc:dd:ee:ff", "192.168.1.255")])
    assert f"Waking {name}..." in shown_text(window)
    window.wake_failed()  # two minutes went by
    assert "didn't wake. It may be shut down instead of asleep" in shown_text(window)
    button(window, f"Wake {name}").click()
    window.agent_up({"api_version": API_VERSION})
    assert "Awake" in shown_text(window)
    wait_for(qapp, lambda: window.screen_name == "All servers", 5)


def test_no_wake_button_without_a_way_to_wake(qapp, agent_http, cable):
    pc = FakePC()
    window = window_on(qapp, agent_http, pc, place="100.64.0.1")
    window.agent_down()
    assert not any(t.startswith("Wake") for t in shown_buttons(window))
