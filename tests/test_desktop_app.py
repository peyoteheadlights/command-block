"""The desktop app, driven like a person would, against the real agent
running in-process with the fake Minecraft servers. Windows itself is
pretend (tests/fake_pc.py)."""

from __future__ import annotations

import http.server
import json
import ssl
import threading
import time

import pytest

from .conftest import PASSWORD
from .fake_pc import FakePC

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")
from PySide6 import QtCore  # noqa: E402

from desktop.agent import AgentClient, WrongCertificate, pinned_context  # noqa: E402
from desktop.window import MainWindow  # noqa: E402


def wait_for(qapp, condition, timeout=15.0, pause=0.02):
    """pause: asking the agent itself needs a longer one, or it says "too
    many tries at once"."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition():
            return
        time.sleep(pause)
    raise AssertionError("timed out waiting for the window")


def shown_text(window) -> str:
    return " ".join(
        w.text()
        for w in window.findChildren(QtWidgets.QLabel)
        if w.isVisibleTo(window) and w.text()
    )


def button(window, text, index=0):
    found = [
        b
        for b in window.findChildren(QtWidgets.QPushButton)
        if b.isVisibleTo(window) and b.text() == text
    ]
    assert found, f"no visible {text!r} button"
    return found[index]


@pytest.fixture(autouse=True)
def close_windows(qapp, request):
    """Each test's windows close at its end, before the agent shuts down, so
    nothing calls an agent that has already stopped."""
    if "agent_http" in request.fixturenames:
        request.getfixturevalue("agent_http")
    yield
    from PySide6.QtCore import QThreadPool

    for window in qapp.topLevelWidgets():
        getattr(window, "quit_app", window.close)()  # X only hides the main window
        window.deleteLater()
    QThreadPool.globalInstance().waitForDone(5000)
    qapp.processEvents()


def open_window(qapp, agent_http, pc=None, dark=False):
    window = MainWindow(AgentClient(agent_http), pc or FakePC(), dark=dark)
    window.show()
    qapp.processEvents()
    return window


def sign_in(qapp, window, username="admin", password=PASSWORD, remember=False):
    window.findChild(QtWidgets.QLineEdit, "username").setText(username)
    window.findChild(QtWidgets.QLineEdit, "password").setText(password)
    window.findChild(QtWidgets.QCheckBox, "remember").setChecked(remember)
    button(window, "Sign in").click()


def on_all_servers(qapp, window):
    wait_for(qapp, lambda: window.screen_name == "All servers" and "Creative" in shown_text(window))


def sign_out(qapp, window):
    """Sign out is in the gear's Your account."""
    window.tools["gear"].click()
    window.open_list("PC settings", "account")
    wait_for(qapp, lambda: "Your account" in shown_text(window.gear))
    button(window.gear, "Sign out").click()


def owner(agent_http) -> dict:
    """Headers for a separate owner sign-in, to look at the agent from outside."""
    token = agent_http.post(
        "/api/auth/login", json={"username": "admin", "password": PASSWORD}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def desktop_session(agent_http, headers) -> dict:
    rows = agent_http.get("/api/sessions", headers=headers).json()["sessions"]
    [row] = [r for r in rows if r["label"] == "Desktop app on TEST-PC"]
    return row


def test_signing_in_shows_every_server_with_its_state(qapp, agent_http):
    window = open_window(qapp, agent_http)
    assert window.screen_name == "Sign-in"
    sign_in(qapp, window)
    wait_for(qapp, lambda: window.screen_name == "All servers" and "Creative" in shown_text(window))
    text = shown_text(window)
    assert "Survival" in text and "Creative" in text
    assert "Offline" in text


def test_a_wrong_password_says_so_and_stays_on_sign_in(qapp, agent_http):
    window = open_window(qapp, agent_http)
    sign_in(qapp, window, password="not it")
    wait_for(qapp, lambda: "password" in shown_text(window).lower())
    assert window.screen_name == "Sign-in"


def test_start_on_all_servers_starts_that_server(qapp, agent_http):
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    wait_for(qapp, lambda: window.screen_name == "All servers" and "Creative" in shown_text(window))
    button(window, "Start", 0).click()

    token = agent_http.post(
        "/api/auth/login", json={"username": "admin", "password": PASSWORD}
    ).json()["token"]

    def started():
        rows = agent_http.get("/api/servers", headers={"Authorization": f"Bearer {token}"}).json()[
            "servers"
        ]
        return any(r["state"] in ("STARTING", "ONLINE") for r in rows)

    wait_for(qapp, started)
    wait_for(qapp, lambda: "Starting" in shown_text(window) or "Online" in shown_text(window))
    for row in agent_http.get("/api/servers", headers={"Authorization": f"Bearer {token}"}).json()[
        "servers"
    ]:
        if row["state"] != "OFFLINE":
            agent_http.post(
                f"/api/servers/{row['id']}/server/stop",
                headers={"Authorization": f"Bearer {token}"},
            )


@pytest.fixture
def tls_server(tmp_path):
    """A tiny HTTPS server with a certificate from a local CA, like the agent's."""
    from agent.security import certs, tls

    issued = certs.issue_server_certificate(tmp_path, ["localhost"], ["127.0.0.1"])
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(issued["cert_path"], issued["key_path"])

    class Answer(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Answer)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fingerprint = tls.fingerprint(issued["cert_path"]).replace(":", "").lower()
    yield server.server_address[1], fingerprint
    server.shutdown()


def test_the_right_certificate_is_trusted(tls_server):
    port, fingerprint = tls_server
    client = AgentClient.connect("127.0.0.1", port, fingerprint)
    assert client.http.get("/").json() == {"ok": True}


def test_a_wrong_certificate_is_refused_before_anything_is_sent(tls_server):
    port, _ = tls_server
    with pytest.raises(WrongCertificate):
        pinned_context("127.0.0.1", port, "0" * 64)


def test_every_route_the_desktop_app_uses_exists_and_is_typed():
    from pathlib import Path

    from .test_api_contract import routes

    current = routes()
    listed = (Path(__file__).resolve().parent.parent / "desktop" / "api-routes.txt").read_text(
        encoding="utf-8"
    )
    used = [
        line.strip() for line in listed.splitlines() if line.strip() and not line.startswith("#")
    ]
    for line in used:
        name = line.removesuffix(" file")
        assert name in current, f"the desktop app uses {name}, which is gone"
        assert name != line or current[name].response_model is not None, name


def test_keep_me_signed_in_lasts_until_signed_out_and_reopens_on_all_servers(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window, remember=True)
    on_all_servers(qapp, window)
    assert list(pc.saved.values()) == [window.agent.token]
    session = desktop_session(agent_http, owner(agent_http))
    assert session["expires_at"] is None

    reopened = open_window(qapp, agent_http, pc)
    on_all_servers(qapp, reopened)


def test_without_keep_me_signed_in_nothing_is_saved_and_the_session_ends(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    assert pc.saved == {}
    session = desktop_session(agent_http, owner(agent_http))
    assert session["expires_at"] is not None and session["expires_at"] < time.time() + 2 * 86400
    assert open_window(qapp, agent_http, pc).screen_name == "Sign-in"


def test_signing_out_forgets_the_sign_in_and_leaves_servers_running(qapp, agent_http):
    headers = owner(agent_http)
    server = agent_http.get("/api/servers", headers=headers).json()["servers"][0]["id"]
    agent_http.post(f"/api/servers/{server}/server/start", headers=headers)
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window, remember=True)
    on_all_servers(qapp, window)
    token = window.agent.token

    sign_out(qapp, window)
    wait_for(qapp, lambda: window.screen_name == "Sign-in")
    assert pc.saved == {}
    gone = agent_http.get("/api/servers", headers={"Authorization": f"Bearer {token}"})
    assert gone.status_code == 401
    rows = agent_http.get("/api/servers", headers=headers).json()["servers"]
    assert next(r for r in rows if r["id"] == server)["state"] in ("STARTING", "ONLINE")
    agent_http.post(f"/api/servers/{server}/server/stop", headers=headers)


def test_signing_this_pc_out_from_another_device_locks_the_app(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window, remember=True)
    on_all_servers(qapp, window)
    headers = owner(agent_http)
    session = desktop_session(agent_http, headers)
    agent_http.delete(f"/api/sessions/{session['id']}", headers=headers)

    window.refresh()  # its next request
    wait_for(qapp, lambda: window.screen_name == "Sign-in")
    assert "signed out" in shown_text(window)
    assert pc.saved == {}


def test_no_token_is_ever_logged(qapp, agent_http, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    window = open_window(qapp, agent_http)
    sign_in(qapp, window, remember=True)
    on_all_servers(qapp, window)
    token = window.agent.token
    sign_out(qapp, window)
    wait_for(qapp, lambda: window.screen_name == "Sign-in")
    assert token and token not in caplog.text


# ------------------------------------------------------------- the server tab
def open_server(qapp, window, name):
    binder = window.findChild(QtWidgets.QTabBar, "binder")
    binder.setCurrentIndex(next(i for i in range(binder.count()) if binder.tabText(i) == name))
    wait_for(
        qapp,
        lambda: window.screen_name == "Server" and name in shown_text(window) and page_tabs(window),
    )


def page_tabs(window) -> list[str]:
    return [
        b.text().strip()
        for b in window.findChildren(QtWidgets.QToolButton, "pageTab")
        if b.isVisibleTo(window)
    ]


def signed_in_on(qapp, agent_http, name="Survival", pc=None):
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    open_server(qapp, window, name)
    return window


def test_simple_shows_six_page_tabs_and_technical_eight(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    assert page_tabs(window) == [
        "Overview",
        "Players and chat",
        "Backups",
        "Mods",
        "Schedules",
        "Settings",
    ]
    button(window, "Technical").click()
    wait_for(qapp, lambda: len(page_tabs(window)) == 8)
    assert page_tabs(window) == [
        "Overview",
        "Players and chat",
        "Console",
        "Backups",
        "Mods",
        "Schedules",
        "Performance",
        "Settings",
    ]
    wait_for(  # saved by a background task
        qapp,
        lambda: (
            agent_http.get("/api/account/preferences", headers=owner(agent_http)).json()["mode"]
            == "technical"
        ),
        pause=0.2,
    )


def test_the_window_takes_the_servers_color(qapp, agent_http):
    from agent import colors
    from desktop import theme

    window = signed_in_on(qapp, agent_http)
    rows = agent_http.get("/api/servers", headers=owner(agent_http)).json()["servers"]
    color = {r["name"]: r["color"] for r in rows}
    themes = json.loads((theme.SHARED / "theme.json").read_text(encoding="utf-8"))
    survival = colors.derive_from(color["Survival"], themes["light"], "light")["accent-band"]
    creative = colors.derive_from(color["Creative"], themes["light"], "light")["accent-band"]
    assert survival in window.styleSheet()
    open_server(qapp, window, "Creative")
    assert creative in window.styleSheet() and survival not in window.styleSheet()


def test_start_stop_and_restart_work_from_the_header(qapp, agent_http):
    headers = owner(agent_http)

    def state():
        rows = agent_http.get("/api/servers", headers=headers).json()["servers"]
        return next(r["state"] for r in rows if r["name"] == "Survival")

    def press(words):
        window.refresh()
        wait_for(qapp, lambda: any(b.text() == words and b.isEnabled() for b in header_buttons()))
        next(b for b in header_buttons() if b.text() == words).click()

    def header_buttons():
        return window.server_screen.header.findChildren(QtWidgets.QPushButton)

    window = signed_in_on(qapp, agent_http)
    press("Start")
    wait_for(qapp, lambda: state() == "ONLINE", timeout=30, pause=0.3)

    def starts():
        events = agent_http.get("/api/servers/survival/events?limit=50", headers=headers).json()
        return sum(e["type"] == "server_started" for e in events["events"])

    wait_for(qapp, lambda: starts() == 1, pause=0.3)
    press("Restart")
    wait_for(qapp, lambda: starts() == 2 and state() == "ONLINE", timeout=30, pause=0.3)
    press("Stop")
    wait_for(qapp, lambda: state() == "OFFLINE", timeout=30, pause=0.3)


def test_paused_restarts_can_be_allowed_again_from_the_header(qapp, agent_http):
    server = agent_http.agent.app.state.core.servers["survival"].server
    server.auto_restart_blocked = True
    server.auto_restart_block_reason = "3 crashes within 10 minutes"
    window = signed_in_on(qapp, agent_http)
    header = window.server_screen.header
    wait_for(qapp, lambda: "3 crashes within 10 minutes" in header.notice.text())
    allow = header.notice_buttons["clear-crash-block"]
    assert allow.isVisibleTo(window) and allow.text() == "Allow restarts"
    allow.click()
    wait_for(qapp, lambda: not server.auto_restart_blocked)
    wait_for(qapp, lambda: not header.notice.isVisibleTo(window))


def test_a_restart_countdown_shows_with_cancel_and_restart_now(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    header = window.server_screen.header
    header.show_status({"restart_in": 30})
    assert header.notice.text() == "Restarting in 30 seconds."
    shown = [a for a, b in header.notice_buttons.items() if b.isVisibleTo(window)]
    assert shown == ["cancel-restart", "restart-now"]
    header.show_status({})
    assert not header.notice.isVisibleTo(window)


def test_undo_and_redo_are_named_and_undo_on_the_agent(qapp, agent_http):
    headers = owner(agent_http)
    base = "/api/servers/creative"
    first = agent_http.post(f"{base}/backups", json={"name": "first"}, headers=headers).json()
    agent_http.post(
        f"{base}/backups/{first['id']}/restore", json={"confirm": True}, headers=headers
    )
    title = agent_http.get(f"{base}/changes", headers=headers).json()["undo"][0]["title"]

    window = signed_in_on(qapp, agent_http, "Creative")
    window.confirm = lambda *words: True  # the "Undo this?" question, answered yes
    wait_for(
        qapp,
        lambda: any(
            title in b.text()
            for b in window.findChildren(QtWidgets.QPushButton)
            if b.isVisibleTo(window)
        ),
    )
    undo = next(b for b in window.findChildren(QtWidgets.QPushButton) if title in b.text())
    assert undo.text().startswith("Undo")
    undo.click()
    wait_for(
        qapp,
        lambda: agent_http.get(f"{base}/changes", headers=headers).json()["redo"],
        pause=0.3,
    )
    window.refresh()
    wait_for(
        qapp,
        lambda: any(
            a.text().startswith("Redo") and title in a.text()
            for a in window.server_screen.header.menu.actions()
        ),
    )
    redo = next(
        a for a in window.server_screen.header.menu.actions() if a.text().startswith("Redo")
    )
    redo.trigger()
    wait_for(
        qapp,
        lambda: agent_http.get(f"{base}/changes", headers=headers).json()["undo"],
        pause=0.3,
    )


def test_a_narrow_window_puts_the_right_panel_under_the_overview(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    panel = window.server_screen.panel
    window.resize(1400, 800)
    wait_for(
        qapp,
        lambda: panel.isVisibleTo(window) and panel.mapTo(window, panel.rect().topLeft()).x() > 900,
    )
    window.resize(900, 800)
    wait_for(
        qapp,
        lambda: panel.isVisibleTo(window) and panel.mapTo(window, panel.rect().topLeft()).x() < 100,
    )


def test_every_control_has_a_name_and_can_be_reached_with_tab(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    controls = [
        w
        for w in window.findChildren(QtWidgets.QAbstractButton)
        + window.findChildren(QtWidgets.QLineEdit)
        + window.findChildren(QtWidgets.QTabBar)
        if w.isVisibleTo(window)
    ]
    assert len(controls) >= 12
    for w in controls:
        name = w.accessibleName() or getattr(w, "text", lambda: "")()
        assert name.strip(), f"{type(w).__name__} {w.objectName()} has no accessible name"
        assert w.focusPolicy() & QtCore.Qt.FocusPolicy.TabFocus, name


def test_text_grows_with_the_windows_text_size(qapp, agent_http):
    pc = FakePC()
    pc.scale = 2.25
    window = signed_in_on(qapp, agent_http, pc=pc)
    assert "22.5pt" in window.styleSheet()
    name = window.server_screen.header.name
    assert name.font().pointSizeF() > 2 * 20


# ------------------------------------------------------------- Overview in Simple
def visible_buttons(window) -> list:
    return [b for b in window.findChildren(QtWidgets.QPushButton) if b.isVisibleTo(window)]


def test_a_suggestion_shows_with_its_fix_button_and_the_button_does_it(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    wait_for(qapp, lambda: "Take a first backup" in shown_text(window))
    fix = next(
        b
        for b in visible_buttons(window)
        if b.text() == "Back up now" and b.property("suggestion") == "no_backup"
    )
    fix.click()
    wait_for(qapp, lambda: window.server_screen.current_page == "Backups")


def test_with_no_suggestions_the_empty_line_fits_the_servers_state(qapp, agent_http):
    headers = owner(agent_http)
    base = "/api/servers/survival"
    for rec in agent_http.get(f"{base}/recommendations", headers=headers).json()["recommendations"]:
        agent_http.post(
            f"{base}/recommendations/{rec['id']}", json={"action": "dismiss"}, headers=headers
        )
    window = signed_in_on(qapp, agent_http)
    wait_for(qapp, lambda: "All good. The server is ready to start :)" in shown_text(window))
    agent_http.post(f"{base}/server/start", json={}, headers=headers)
    wait_for(
        qapp,
        lambda: (
            agent_http.get("/api/servers", headers=headers).json()["servers"][0]["state"]
            == "ONLINE"
        ),
        timeout=30,
        pause=0.3,
    )
    for rec in agent_http.get(f"{base}/recommendations", headers=headers).json()["recommendations"]:
        agent_http.post(
            f"{base}/recommendations/{rec['id']}", json={"action": "dismiss"}, headers=headers
        )
    window.refresh()
    window.load_server_parts()
    wait_for(qapp, lambda: "The server is running at its best :D" in shown_text(window))
    agent_http.post(f"{base}/server/stop", json={}, headers=headers)


def test_the_four_cards_are_the_same_size(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    cards = window.server_screen.overview_cards
    assert len(cards) == 4
    wait_for(
        qapp,
        lambda: (
            "Last checked backup" in shown_text(window) or "No backups yet" in shown_text(window)
        ),
    )
    wait_for(qapp, lambda: len({(c.width(), c.height()) for c in cards}) == 1)
    titles = shown_text(window)
    for title in ("Server address", "Last backup", "Mods", "Your world"):
        assert title in titles


def test_a_new_server_shows_the_setup_checklist(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    wait_for(qapp, lambda: "Getting started" in shown_text(window))
    wait_for(qapp, lambda: "Take a backup" in shown_text(window))


def test_the_owner_sees_the_ask_once_and_donate_opens_the_support_link(
    qapp, agent_http, monkeypatch
):
    import json

    from agent import supportask
    from desktop.theme import SHARED

    from .fake_pc import FakePC

    monkeypatch.setattr(supportask, "something_wrong", lambda core, now: False)
    core = agent_http.agent.app.state.core
    core.db.set_setting(supportask.STATE_KEY, {"first_start": 1.0})
    pc = FakePC()
    window = signed_in_on(qapp, agent_http, pc=pc)
    wait_for(qapp, lambda: "Enjoying Command Block?" in shown_text(window))
    button(window, "Donate").click()
    links = json.loads((SHARED / "links.json").read_text(encoding="utf-8"))
    assert pc.opened[-1] == links["sponsors"]
    wait_for(qapp, lambda: core.db.get_setting(supportask.STATE_KEY).get("answer") == "donate")
    assert "Enjoying Command Block?" not in shown_text(window)


def test_a_hint_shows_on_hover_on_tab_and_space_and_to_narrator(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    hint = next(
        h for h in window.findChildren(QtWidgets.QToolButton, "hint") if h.isVisibleTo(window)
    )
    words = hint.toolTip()
    assert len(words) > 20
    assert hint.accessibleDescription() == words
    assert hint.focusPolicy() & QtCore.Qt.FocusPolicy.TabFocus
    hint.setFocus()
    from PySide6.QtGui import QKeyEvent

    for kind in (QtCore.QEvent.Type.KeyPress, QtCore.QEvent.Type.KeyRelease):
        QtWidgets.QApplication.sendEvent(
            hint, QKeyEvent(kind, QtCore.Qt.Key.Key_Space, QtCore.Qt.KeyboardModifier.NoModifier)
        )
    assert QtWidgets.QToolTip.text() == words


def test_technical_mode_has_no_suggestions_card(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    wait_for(qapp, lambda: "Take a first backup" in shown_text(window))
    button(window, "Technical").click()
    wait_for(qapp, lambda: "Take a first backup" not in shown_text(window))


# ------------------------------------------------------------- Technical Overview
def technical_on(qapp, agent_http, name="Survival"):
    window = signed_in_on(qapp, agent_http, name)
    button(window, "Technical").click()
    wait_for(qapp, lambda: window.server_screen.technical_overview.isVisible())
    return window


def test_the_graphs_draw_the_agents_samples_with_units_and_gaps(qapp, agent_http):
    from desktop.graphs import gaps

    headers = owner(agent_http)
    [row] = [
        r
        for r in agent_http.get("/api/servers", headers=headers).json()["servers"]
        if r["name"] == "Survival"
    ]
    db = agent_http.agent.app.state.core.db
    now = time.time()
    db.insert("metrics", {"server_id": row["id"], "ts": now - 3000, "cpu_percent": 5.0})  # off
    for seconds in (600, 590, 580):
        db.insert(
            "metrics",
            {
                "server_id": row["id"],
                "ts": now - seconds,
                "cpu_percent": 30.0,
                "tps": 19.5,
                "mspt": 12.0,
                "proc_ram_mb": 2048.0,
            },
        )
    window = technical_on(qapp, agent_http)
    graphs = window.server_screen.technical_overview.graphs
    wait_for(qapp, lambda: any(v for _t, v in graphs["tps"].points))
    series = agent_http.get(
        f"/api/servers/{row['id']}/performance?hours=1&storage=false", headers=headers
    ).json()["series"]["points"]
    # The buckets move with the clock, so only the samples are compared.
    drawn = [v for _t, v in graphs["tps"].points if v is not None]
    assert drawn == [p["tps"] for p in series if p["tps"] is not None]
    assert 2.0 in [v for _t, v in graphs["proc_ram_mb"].points]  # 2048 MB shown as GB
    assert graphs["mspt"].axis_labels()[-1].endswith("ms")
    assert graphs["proc_ram_mb"].axis_labels()[-1].endswith("GB")
    assert graphs["cpu_percent"].axis_labels()[-1].endswith("%")
    tps = graphs["tps"]
    reasons = {g[2] for g in gaps(tps.points, tps.start, tps.end, tps.gap)}
    assert reasons == {"desktop.graph.server_off", "desktop.graph.agent_gap"}
    # Narrator reads the latest value, the lowest and the highest.
    assert "19.5" in tps.accessibleDescription()
    tps.grab()  # paints without errors


def test_the_graphs_only_ask_for_samples_while_they_can_be_seen(qapp, agent_http):
    window = technical_on(qapp, agent_http)
    timer = window.server_screen.technical_overview.timer
    assert timer.isActive()
    window.server_screen.show_page("Console")
    wait_for(qapp, lambda: not timer.isActive())
    window.server_screen.show_page("Overview")
    wait_for(qapp, timer.isActive)
    button(window, "Simple").click()
    wait_for(qapp, lambda: not timer.isActive())


def test_with_the_agent_cut_off_every_live_value_is_unknown(qapp, agent_http):
    """The no-fabrication rule in the desktop app: no last-known number
    stays on screen once the agent can't be asked."""
    import httpx

    window = technical_on(qapp, agent_http)
    technical = window.server_screen.technical_overview
    wait_for(qapp, lambda: all(v.text() for v in technical.values.values()))

    def cut_off(*_args, **_kwargs):
        raise httpx.ConnectError("cut off")

    window.agent.http.request = cut_off
    window.load_technical()
    wait_for(qapp, lambda: all(v.text() == "Unknown" for v in technical.values.values()))
    assert all(not g.points for g in technical.graphs.values())


# ------------------------------------------------------------- live feed and alerts
def run_on_agent(agent_http, server_id, action, headers, **body):
    path = f"/api/servers/{server_id}/server/{action}"
    response = agent_http.post(path, json=body, headers=headers)
    assert response.status_code == 200, response.text


def server_state(agent_http, server_id, headers) -> str:
    rows = agent_http.get("/api/servers", headers=headers).json()["servers"]
    return next(r["state"] for r in rows if r["id"] == server_id)


def bell(window) -> str:
    return window.tools["bell"].text().strip()


def test_a_crash_shows_a_windows_alert_and_a_bell_entry(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    headers = owner(agent_http)
    run_on_agent(agent_http, "survival", "start", headers)
    wait_for(qapp, lambda: server_state(agent_http, "survival", headers) == "ONLINE", 30, 0.2)
    run_on_agent(agent_http, "survival", "command", headers, command="crash")
    wait_for(qapp, lambda: any("crashed" in title for title, _body in pc.alerts), 30)
    window.tools["bell"].click()
    wait_for(qapp, lambda: window.screen_name == "Notifications")
    listed = window.screens["Notifications"].items()
    assert any("Survival crashed" in item for item in listed)


def test_no_windows_alert_while_the_window_shows_that_server(qapp, agent_http):
    pc = FakePC()
    window = signed_in_on(qapp, agent_http, "Survival", pc)
    run_on_agent(agent_http, "survival", "start", owner(agent_http))
    wait_for(qapp, lambda: bell(window) == "1", 30)
    assert pc.alerts == []


def test_clicking_an_alert_opens_its_server_and_several_at_once_open_the_bell(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    headers = owner(agent_http)
    run_on_agent(agent_http, "survival", "start", headers)
    wait_for(qapp, lambda: len(pc.alerts) == 1, 30)
    assert pc.buttons() == ["Open"]  # a server starting has nothing to restart
    pc.click_alert()
    wait_for(qapp, lambda: window.screen_name == "Server" and window.server_id == "survival")
    assert window.server_screen.current_page == "Overview"

    # Alerts that came while the feed was away show as one Windows alert.
    window.binder.setCurrentIndex(0)  # All servers
    found = agent_http.get("/api/alerts?after=0", headers=headers).json()["alerts"]
    window.caught_up_with(
        {"alerts": [dict(a, id=a["id"] + 100 + i) for i, a in enumerate(found * 2)]}
    )
    assert pc.alerts[-1][0] == f"{len(found) * 2} new alerts"
    pc.click_alert()
    wait_for(qapp, lambda: window.screen_name == "Notifications")


def test_a_crash_alert_has_a_restart_button_that_restarts_the_server(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    headers = owner(agent_http)
    run_on_agent(agent_http, "survival", "start", headers)
    wait_for(qapp, lambda: server_state(agent_http, "survival", headers) == "ONLINE", 30, 0.2)
    window.hide()
    run_on_agent(agent_http, "survival", "command", headers, command="crash")
    wait_for(qapp, lambda: any("crashed" in title for title, _body in pc.alerts), 30)
    crashed = next(i for i, (title, _body) in enumerate(pc.alerts) if "crashed" in title)
    assert pc.buttons(crashed) == ["Open", "Restart"]
    asked, real = [], window.agent.server_action
    window.agent.server_action = lambda sid, action: (
        asked.append((sid, action)) or real(sid, action)
    )
    pc.click_alert("Restart", crashed)
    wait_for(qapp, lambda: asked == [("survival", "restart")])
    wait_for(qapp, lambda: server_state(agent_http, "survival", headers) == "ONLINE", 30, 0.2)
    assert not window.isVisible()  # restarting doesn't open the window


def test_a_moderator_gets_no_alerts_for_other_servers(qapp, agent_http):
    headers = owner(agent_http)
    made = agent_http.post(
        "/api/accounts",
        json={"username": "sam", "password": "moderator password", "servers": ["creative"]},
        headers=headers,
    )
    assert made.status_code == 200, made.text
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window, "sam", "moderator password")
    wait_for(qapp, lambda: window.screen_name == "All servers")
    run_on_agent(agent_http, "survival", "start", headers)
    agent_http.post("/api/auth/login", json={"username": "admin", "password": "wrong"})

    def owner_alerts():
        found = agent_http.get("/api/alerts?after=0", headers=headers).json()["alerts"]
        return {a["event"] for a in found}

    wait_for(qapp, lambda: {"server_started", "auth_failure"} <= owner_alerts(), 30, 0.2)
    time.sleep(1.0)  # time for the feed to bring them, if it wrongly would
    qapp.processEvents()
    assert pc.alerts == [] and bell(window) == ""


def test_the_feed_comes_back_after_the_agent_restarts_without_repeats(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    headers = owner(agent_http)
    run_on_agent(agent_http, "survival", "start", headers)
    wait_for(qapp, lambda: len(pc.alerts) == 1, 30)
    agent_http.agent.stop()
    agent_http.agent.start()
    headers = owner(agent_http)
    wait_for(qapp, lambda: window.feed.connected, 30)
    run_on_agent(agent_http, "creative", "start", headers)
    wait_for(qapp, lambda: len(pc.alerts) >= 2, 30)
    time.sleep(1.0)
    qapp.processEvents()
    titles = [title for title, _body in pc.alerts]
    assert len(titles) == len(set(titles)) and len(titles) == 2, titles


# ------------------------------------------------------------- the notification area
def tray_items(pc) -> dict[str, list[str]]:
    """The notification area menu: each server's submenu (and the menu's own
    items under "") as the words on them."""
    found: dict[str, list[str]] = {"": []}
    for action in pc.tray_menu.actions():
        if action.menu():
            found[action.text()] = [a.text() for a in action.menu().actions()]
        elif action.text():
            found[""].append(action.text())
    return found


def tray_action(pc, server, words):
    for action in pc.tray_menu.actions():
        if action.menu() and action.text().startswith(server):
            return next(a for a in action.menu().actions() if a.text() == words)
    raise AssertionError(f"no {words!r} for {server} in the menu")


def test_closing_hides_the_window_and_says_so_only_the_first_time(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    window.close()
    qapp.processEvents()
    assert not window.isVisible()
    assert len(pc.alerts) == 1  # "Command Block is still running here"
    window.show()
    window.close()
    qapp.processEvents()
    assert len(pc.alerts) == 1
    # A new window on this PC remembers it was told.
    again = open_window(qapp, agent_http, pc)
    again.close()
    assert len(pc.alerts) == 1


def test_quitting_leaves_every_server_running(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    headers = owner(agent_http)
    run_on_agent(agent_http, "survival", "start", headers)
    wait_for(qapp, lambda: server_state(agent_http, "survival", headers) == "ONLINE", 30, 0.2)
    wait_for(qapp, lambda: "Quit" in tray_items(pc)[""])
    next(a for a in pc.tray_menu.actions() if a.text() == "Quit").trigger()
    qapp.processEvents()
    time.sleep(1.0)
    assert server_state(agent_http, "survival", headers) == "ONLINE"


def test_the_menu_lists_each_server_and_starts_it(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    wait_for(qapp, lambda: len(tray_items(pc)) == 3)
    items = tray_items(pc)
    assert items[""] == ["Open Command Block", "Quit"]
    assert items["Survival: Offline"] == ["Start", "Open"]
    tray_action(pc, "Survival", "Start").trigger()
    headers = owner(agent_http)
    wait_for(qapp, lambda: server_state(agent_http, "survival", headers) == "ONLINE", 30, 0.2)
    wait_for(qapp, lambda: "Survival: Online" in tray_items(pc))
    tray_action(pc, "Survival", "Open").trigger()
    wait_for(qapp, lambda: window.screen_name == "Server" and window.server_id == "survival")


def test_a_moderator_sees_only_their_servers_and_buttons_in_the_menu(qapp, agent_http):
    made = agent_http.post(
        "/api/accounts",
        json={
            "username": "sam",
            "password": "moderator password",
            "servers": ["creative"],
            "permissions": ["server.view"],
        },
        headers=owner(agent_http),
    )
    assert made.status_code == 200, made.text
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window, "sam", "moderator password")
    wait_for(qapp, lambda: "Creative: Offline" in tray_items(pc))
    assert set(tray_items(pc)) == {"", "Creative: Offline"}
    assert tray_items(pc)["Creative: Offline"] == ["Open"]


def test_the_icon_shows_a_crash_until_the_server_runs_again(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    headers = owner(agent_http)
    run_on_agent(agent_http, "survival", "start", headers)
    wait_for(qapp, lambda: server_state(agent_http, "survival", headers) == "ONLINE", 30, 0.2)
    assert "crashed" not in pc.tray_words
    run_on_agent(agent_http, "survival", "command", headers, command="crash")
    wait_for(qapp, lambda: "Survival crashed" in pc.tray_words, 30)
    run_on_agent(agent_http, "survival", "start", headers)
    wait_for(qapp, lambda: "crashed" not in pc.tray_words, 30)


def test_open_when_windows_starts_is_on_by_default_on_the_agents_pc(qapp, agent_http):
    pc = FakePC()
    open_window(qapp, agent_http, pc)
    assert pc.starts_with_windows is True
    pc = FakePC()
    pc.starts_with_windows = False  # turned off before: left off
    pc.settings["start_with_windows_set"] = True
    open_window(qapp, agent_http, pc)
    assert pc.starts_with_windows is False


def test_a_hidden_window_runs_no_timers(qapp, agent_http):
    window = technical_on(qapp, agent_http)
    window.close()  # hides it

    def running():
        return [t for t in window.findChildren(QtCore.QTimer) if t.isActive()]

    # (a change the feed just brought can still be waiting to be read)
    wait_for(qapp, lambda: not running(), 3)
    time.sleep(1.0)
    qapp.processEvents()
    assert not running()


# ------------------------------------------------------------- agent not running, versions
def test_with_the_agent_stopped_the_window_says_so_and_comes_back(qapp, agent_http, monkeypatch):
    monkeypatch.setattr(MainWindow, "STILL_DOWN_MS", 500)  # 3 minutes on a real PC
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    agent_http.agent.stop()
    wait_for(qapp, lambda: window.screen_name == "Agent not running", 30)
    assert "The agent on this PC isn't running" in shown_text(window)
    wait_for(qapp, lambda: "may be off or asleep" in shown_text(window), 10)
    agent_http.agent.start()
    wait_for(qapp, lambda: window.screen_name == "All servers", 40)


def test_start_the_agent_asks_windows_to_start_it(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    agent_http.agent.stop()
    wait_for(qapp, lambda: window.screen_name == "Agent not running", 30)
    button(window, "Start the agent").click()
    assert pc.agent_starts == 1


def test_an_unreachable_agent_at_start_still_opens_the_window():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]  # nothing listens here once it's closed
    agent = AgentClient.connect("127.0.0.1", port, "00" * 32)
    from desktop.agent import Unreachable

    with pytest.raises(Unreachable):
        agent.health()


def test_a_newer_app_with_the_same_api_version_keeps_working_and_says_so(
    qapp, agent_http, monkeypatch
):
    monkeypatch.setattr("desktop.window.APP_VERSION", "99.0.0")
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    wait_for(qapp, lambda: "older than this app" in shown_text(window))
    assert "Survival" in shown_text(window)


def test_a_different_api_version_stops_the_app(qapp, agent_http, monkeypatch):
    from agent import API_VERSION

    monkeypatch.setattr("desktop.window.API_VERSION", API_VERSION + 1)
    window = open_window(qapp, agent_http)
    wait_for(qapp, lambda: window.screen_name == "Update needed")
    assert "needs to update Command Block" in shown_text(window)
    monkeypatch.setattr("desktop.window.API_VERSION", API_VERSION - 1)
    window = open_window(qapp, agent_http)
    wait_for(qapp, lambda: window.screen_name == "Update needed")
    assert "Update Command Block to keep using it" in shown_text(window)


# ------------------------------------------------------------- the Tailscale step
def tailscale_is(
    monkeypatch,
    installed=True,
    connected=False,
    account="jo@example.com",
    address="100.101.102.103",
    error=None,
):
    """What Tailscale on the agent's PC says, for the agent's minute check,
    the step's state and the pairing code alike."""
    from agent import tailscale, tailscale_watch

    report = {
        "cli_found": installed,
        "connected": connected,
        "addresses": [address] if connected else [],
        "dns_name": f"pc-{address.replace('.', '-')}.tail1.ts.net" if connected else None,
        "account": account if connected else None,
        "error": error,
    }
    monkeypatch.setattr(tailscale, "tailscale_status", lambda: report)
    monkeypatch.setattr(tailscale_watch, "tailscale_status", lambda: report)


def tailscale_ready(agent_http, monkeypatch):
    """HTTPS on (the move needs it), a certificate fingerprint for the
    pairing code, and a restart that really restarts the test agent."""
    from agent import pairing

    live = agent_http.agent
    live.config.set("tls.enabled", True)
    monkeypatch.setattr(pairing, "certificate_fingerprint", lambda _c: "ab" * 32)

    def restart():
        threading.Thread(target=lambda: (live.stop(), live.start()), daemon=True).start()

    live.app.state.core.restart = restart


def open_step(qapp, agent_http, pc=None):
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    window.show_screen("Tailscale")
    return window


def test_the_step_installs_signs_in_and_only_then_says_phones_can_reach(
    qapp, agent_http, monkeypatch
):
    tailscale_ready(agent_http, monkeypatch)
    tailscale_is(monkeypatch, installed=False)
    pc = FakePC()
    window = open_step(qapp, agent_http, pc)
    wait_for(qapp, lambda: "Install Tailscale" in [b.text() for b in visible_buttons(window)])
    button(window, "Install Tailscale").click()
    wait_for(qapp, lambda: pc.sign_in_page in pc.opened)
    assert pc.tailscale_installs == 1 and pc.tailscale_ups == 1
    assert "Sign in in your browser" in shown_text(window)
    time.sleep(0.5)
    qapp.processEvents()
    assert "Phones can now reach" not in shown_text(window)  # not connected yet
    assert agent_http.agent.app.state.core.config.network.host == "127.0.0.1"
    tailscale_is(monkeypatch, connected=True)
    wait_for(qapp, lambda: "Phones can now reach this PC" in shown_text(window), 40, 0.1)
    assert "jo@example.com" in shown_text(window)
    wait_for(qapp, lambda: bool(window.tailscale_step.qr.rows))  # the pairing QR code
    assert agent_http.agent.app.state.core.config.network.host == "100.101.102.103"


def test_each_failure_says_why_with_retry_and_skip(qapp, agent_http, monkeypatch):
    from desktop import tailscale_step

    tailscale_ready(agent_http, monkeypatch)
    tailscale_is(monkeypatch, installed=False)
    pc = FakePC()
    pc.install_problem = "Windows didn't get permission to install Tailscale."
    window = open_step(qapp, agent_http, pc)
    wait_for(qapp, lambda: "Install Tailscale" in [b.text() for b in visible_buttons(window)])
    button(window, "Install Tailscale").click()
    wait_for(qapp, lambda: pc.install_problem in shown_text(window))
    assert {"Retry", "Skip for now"} <= {b.text() for b in visible_buttons(window)}
    assert pc.tailscale_ups == 0  # nothing ran after the failed install
    # Signing in never finishes.
    pc.install_problem = None
    monkeypatch.setattr(tailscale_step, "GIVE_UP_AFTER", 1)
    button(window, "Retry").click()
    wait_for(qapp, lambda: "Install Tailscale" in [b.text() for b in visible_buttons(window)])
    button(window, "Install Tailscale").click()
    wait_for(qapp, lambda: "The sign-in didn't finish" in shown_text(window), 20, 0.1)
    assert {"Retry", "Skip for now"} <= {b.text() for b in visible_buttons(window)}


def test_another_users_tailscale_is_named_and_left_alone(qapp, agent_http, monkeypatch):
    tailscale_ready(agent_http, monkeypatch)
    tailscale_is(
        monkeypatch, connected=None, error='Tailscale already in use by "PC\\bob", pid 4242'
    )
    pc = FakePC()
    window = open_step(qapp, agent_http, pc)
    wait_for(qapp, lambda: "another Windows user" in shown_text(window))
    assert "PC\\bob" in shown_text(window)
    assert "Skip for now" in [b.text() for b in visible_buttons(window)]
    assert (pc.tailscale_installs, pc.tailscale_ups, pc.tailscale_logouts) == (0, 0, 0)


def test_a_different_account_shows_the_new_pairing_code(qapp, agent_http, monkeypatch):
    tailscale_ready(agent_http, monkeypatch)
    agent_http.agent.config.set("network.host", "100.101.102.103")
    tailscale_is(monkeypatch, connected=True)
    pc = FakePC()
    window = open_step(qapp, agent_http, pc)
    wait_for(qapp, lambda: bool(window.tailscale_step.qr.rows))
    before = list(window.tailscale_step.qr.rows)
    tailscale_is(monkeypatch, connected=False)
    button(window, "Use a different account").click()
    wait_for(qapp, lambda: pc.sign_in_page in pc.opened)
    assert pc.tailscale_logouts == 1 and pc.tailscale_ups == 1
    tailscale_is(monkeypatch, connected=True, account="jo@example.com", address="100.64.0.9")
    wait_for(qapp, lambda: "jo@example.com" in shown_text(window), 40, 0.1)
    wait_for(qapp, lambda: window.tailscale_step.qr.rows not in ([], before))
    assert agent_http.agent.app.state.core.config.network.host == "100.64.0.9"


def test_skipping_leaves_the_yellow_dot_and_the_menu_item(qapp, agent_http, monkeypatch):
    tailscale_ready(agent_http, monkeypatch)
    tailscale_is(monkeypatch, installed=False)
    pc = FakePC()
    window = open_step(qapp, agent_http, pc)
    wait_for(qapp, lambda: "Skip for now" in [b.text() for b in visible_buttons(window)])
    button(window, "Skip for now").click()
    wait_for(qapp, lambda: window.screen_name == "All servers")
    assert "Setup isn't finished" in pc.tray_words  # the yellow dot
    finish = "Finish setup: reach your servers from your phone"
    assert finish in tray_items(pc)[""]
    next(a for a in pc.tray_menu.actions() if a.text() == finish).trigger()
    assert window.screen_name == "Tailscale"


def test_reconnect_runs_tailscale_up_again(qapp, agent_http, monkeypatch):
    tailscale_ready(agent_http, monkeypatch)
    core = agent_http.agent.app.state.core
    core.config.set("network.host", "100.101.102.103")
    tailscale_is(monkeypatch, connected=False)
    core.tailscale.down = True  # the minute check saw it stop (tests/test_tailscale_step.py)
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    window.open_server("survival")
    wait_for(qapp, lambda: "Tailscale stopped" in shown_text(window), 20, 0.1)
    button(window, "Reconnect").click()
    assert window.screen_name == "Tailscale"
    wait_for(qapp, lambda: "Connect Tailscale" in [b.text() for b in visible_buttons(window)])
    button(window, "Connect Tailscale").click()
    wait_for(qapp, lambda: pc.tailscale_ups == 1)


def a_windows_pc(monkeypatch, signer, code=0):
    """The real WindowsPC with its download, signature check and installer
    replaced: what ran is recorded."""
    from desktop.pc import WindowsPC

    pc = WindowsPC()
    ran: list[str] = []
    monkeypatch.setattr(pc, "download_tailscale", lambda folder: folder / "tailscale-setup.msi")
    monkeypatch.setattr(pc, "msi_signer", lambda path: signer)
    monkeypatch.setattr(pc, "install_msi", lambda path: ran.append(path.name) or code)
    return pc, ran


def test_a_setup_not_signed_by_tailscale_is_refused_and_nothing_runs(monkeypatch):
    from desktop.pc import PCError

    for signer in ("Evil Corp", None):  # someone else's, or no valid signature
        pc, ran = a_windows_pc(monkeypatch, signer)
        with pytest.raises(PCError, match="wasn't signed by Tailscale Inc"):
            pc.install_tailscale()
        assert ran == []
    pc, ran = a_windows_pc(monkeypatch, "Tailscale Inc.")
    pc.install_tailscale()
    assert ran == ["tailscale-setup.msi"]


def test_saying_no_to_windows_is_its_own_message(monkeypatch):
    from desktop.pc import CANCELLED, PCError

    pc, _ran = a_windows_pc(monkeypatch, "Tailscale Inc.", code=CANCELLED)
    with pytest.raises(PCError, match="didn't get permission"):
        pc.install_tailscale()
    pc, _ran = a_windows_pc(monkeypatch, "Tailscale Inc.", code=1603)
    with pytest.raises(PCError, match="1603"):
        pc.install_tailscale()


def test_tailscale_up_is_one_fixed_command_and_only_tailscales_page_opens(monkeypatch):
    import io
    import subprocess

    from agent import tailscale
    from desktop.pc import PCError, WindowsPC

    asked: list[list[str]] = []

    class Proc:
        def __init__(self, args, **_kw):
            asked.append(args)
            self.stdout = io.StringIO(said)
            self.returncode = code

        def wait(self):
            return self.returncode

        def kill(self):
            pass

    monkeypatch.setattr(tailscale, "tailscale_binary", lambda: "tailscale.exe")
    monkeypatch.setattr(subprocess, "Popen", Proc)
    said, code = "\nTo authenticate, visit:\n\n\thttps://login.tailscale.com/a/1f2e3d\n", 0
    assert WindowsPC().tailscale_up() == "https://login.tailscale.com/a/1f2e3d"
    assert asked == [["tailscale.exe", "up", "--unattended"]]
    said, code = "To authenticate, visit: https://evil.example/a/1\n", 1
    with pytest.raises(PCError):
        WindowsPC().tailscale_up()  # not Tailscale's page: never opened
    said, code = "", 0
    assert WindowsPC().tailscale_up() is None  # connected without a sign-in


def test_only_tailscales_windows_setup_can_be_downloaded_from_its_site():
    from agent import downloads

    downloads.check_url("https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi")
    downloads.check_url("https://pkgs.tailscale.com/stable/tailscale-setup-1.80.0-amd64.msi")
    for url in (
        "https://pkgs.tailscale.com/stable/",
        "https://pkgs.tailscale.com/unstable/tailscale-setup-latest-amd64.msi",
        "http://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi",
    ):
        with pytest.raises(downloads.DownloadError):
            downloads.check_url(url)


# ------------------------------------------------------------- the installer's one-time sign-in
def test_the_installers_code_signs_in_once_and_opens_the_tailscale_step(
    qapp, agent_http, monkeypatch
):
    from agent.security.auth import write_one_time_sign_in

    tailscale_is(monkeypatch, installed=False)
    pc = FakePC()
    pc.settings["sign_in_username"] = "admin"
    pc.settings["one_time_sign_in"] = write_one_time_sign_in(
        agent_http.agent.app.state.core.config.data_dir
    )
    window = open_window(qapp, agent_http, pc)
    wait_for(qapp, lambda: window.screen_name == "Tailscale")
    assert pc.saved  # Keep me signed in
    assert pc.settings["one_time_sign_in"] == ""
    button(window, "Skip for now").click()
    wait_for(qapp, lambda: window.screen_name == "All servers")


def test_after_ten_minutes_it_is_the_sign_in_with_the_username_filled_in(
    qapp, agent_http, monkeypatch
):
    from agent.security import auth

    code = auth.write_one_time_sign_in(agent_http.agent.app.state.core.config.data_dir)
    later = time.time() + 11 * 60
    monkeypatch.setattr(auth.time, "time", lambda: later)
    pc = FakePC()
    pc.settings.update(sign_in_username="admin", one_time_sign_in=code)
    window = open_window(qapp, agent_http, pc)
    wait_for(qapp, lambda: "Sign in with your password" in shown_text(window))
    assert window.screen_name == "Sign-in"
    assert window.findChild(QtWidgets.QLineEdit, "username").text() == "admin"


# ------------------------------------------------------------- connect to someone else's PC
def connect_window(qapp, pc=None):
    from desktop.connect import ConnectWindow

    reached: list = []
    window = ConnectWindow(pc or FakePC(), reached.append)
    window.reached = reached
    window.show()
    qapp.processEvents()
    return window


def paste(qapp, window, link):
    window.findChild(QtWidgets.QLineEdit, "link").setText(link)
    window.button.click()


def test_a_pasted_pairing_link_connects_and_a_wrong_fingerprint_is_refused(qapp, tls_server):
    port, fingerprint = tls_server
    pc = FakePC()
    window = connect_window(qapp, pc)
    paste(qapp, window, "https://example.com/not-a-link")
    assert "isn't a Command Block link" in shown_text(window)
    paste(qapp, window, f"https://127.0.0.1:{port}/?pair=1&fp={'0' * 64}&api=1")
    wait_for(qapp, lambda: "certificate isn't the one in the pairing link" in shown_text(window))
    assert window.reached == [] and "pc_link" not in pc.settings
    link = f"https://127.0.0.1:{port}/?pair=1&fp={fingerprint}&api=1"
    paste(qapp, window, link)
    wait_for(qapp, lambda: bool(window.reached))
    assert pc.settings["pc_link"] == link  # connects there next time


def test_a_moderators_first_run_sets_up_tailscale_then_opens_the_share_link(qapp):
    pc = FakePC()
    pc.ts_state = {"cli_found": False, "connected": False, "error": "not found"}
    window = connect_window(qapp, pc)
    wait_for(qapp, lambda: "First, Tailscale" in shown_text(window))
    assert not window.findChild(QtWidgets.QLineEdit, "link").isVisible()
    assert window.button.text() == "Install Tailscale"
    window.button.click()
    wait_for(qapp, lambda: pc.tailscale_ups == 1)
    assert pc.tailscale_installs == 1
    wait_for(qapp, lambda: pc.opened == [pc.sign_in_page])
    assert window.button.text() == "Open the sign-in page again"
    pc.ts_state = {"cli_found": True, "connected": True, "error": None}
    window.check_tailscale()  # what the poll does every few seconds
    wait_for(qapp, lambda: window.findChild(QtWidgets.QLineEdit, "link").isVisible())
    # The owner's share link opens in the browser; the pairing link comes next.
    paste(qapp, window, "https://login.tailscale.com/admin/invite/abc123")
    assert pc.opened[-1] == "https://login.tailscale.com/admin/invite/abc123"
    assert "Accept it there, then paste the pairing link" in shown_text(window)
    assert window.reached == []


def test_tailscale_can_be_skipped_and_is_never_set_up_over_someone_elses(qapp):
    pc = FakePC()
    pc.ts_state = {"cli_found": True, "connected": False, "error": None}
    window = connect_window(qapp, pc)
    wait_for(qapp, lambda: window.button.text() == "Connect Tailscale")
    window.skip.click()
    assert window.findChild(QtWidgets.QLineEdit, "link").isVisible()
    pc.ts_state = {"cli_found": True, "connected": False, "error": "in use by another user"}
    other = connect_window(qapp, pc)
    wait_for(qapp, lambda: "used by another Windows user" in shown_text(other))
    assert other.findChild(QtWidgets.QLineEdit, "link").isVisible()
    assert pc.tailscale_installs == 0 and pc.tailscale_ups == 0


def invite_link(agent_http, permissions=("server.view",)) -> str:
    made = agent_http.post(
        "/api/accounts",
        json={"username": "sam", "servers": ["creative"], "permissions": list(permissions)},
        headers=owner(agent_http),
    )
    assert made.status_code == 200, made.text
    from urllib.parse import parse_qs, urlparse

    token = parse_qs(urlparse(made.json()["invite"]["url"]).query)["invite"][0]
    return f"https://pc.example.ts.net:8443/?pair=1&fp={'ab' * 32}&api=1&invite={token}&user=sam"


def test_an_invite_asks_only_for_a_password_and_shows_only_what_sam_may_do(
    qapp, agent_http, monkeypatch
):
    from desktop import connect

    monkeypatch.setattr(connect, "agent_for", lambda found: AgentClient(agent_http))
    monkeypatch.setattr(
        "agent.pairing.pairing_code",
        lambda config: {"ok": True, "url": "https://pc.example.ts.net:8443/?pair=1", "qr": ["1"]},
    )
    pc = FakePC()
    window = connect_window(qapp, pc)
    paste(qapp, window, invite_link(agent_http))
    wait_for(qapp, lambda: window.findChild(QtWidgets.QLineEdit, "username").isVisible())
    assert window.findChild(QtWidgets.QLineEdit, "username").text() == "sam"
    window.findChild(QtWidgets.QLineEdit, "password").setText("sam's own password")
    window.findChild(QtWidgets.QLineEdit, "again").setText("sam's own password")
    window.button.click()
    wait_for(qapp, lambda: bool(window.reached))
    assert pc.saved  # signed in, kept until signed out
    assert "invite" not in pc.settings["pc_link"]
    main = MainWindow(window.reached[0], pc, dark=False)
    main.show()
    wait_for(qapp, lambda: main.screen_name == "All servers" and "Creative" in shown_text(main))
    wait_for(qapp, lambda: main.role == "helper")
    assert "Survival" not in shown_text(main)
    assert "Start" not in [b.text() for b in visible_buttons(main)]  # no server.control


def test_a_used_invite_shows_the_agents_message(qapp, agent_http, monkeypatch):
    from desktop import connect

    monkeypatch.setattr(connect, "agent_for", lambda found: AgentClient(agent_http))
    monkeypatch.setattr(
        "agent.pairing.pairing_code",
        lambda config: {"ok": True, "url": "https://pc.example.ts.net:8443/?pair=1", "qr": ["1"]},
    )
    link = invite_link(agent_http)
    token = link.split("invite=")[1].split("&")[0]
    used = agent_http.post(
        "/api/invite/accept", json={"token": token, "password": "sam's own password"}
    )
    assert used.status_code == 200
    window = connect_window(qapp)
    paste(qapp, window, link)
    wait_for(qapp, lambda: "used already" in shown_text(window))
    assert window.reached == []


# ------------------------------------------------------------- updates
def test_an_update_closes_the_app_on_this_pc_and_it_opens_again_as_it_was(qapp, agent_http):
    from desktop.app import open_after_update

    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    window.feed_message({"type": "event", "event": {"type": "update_closing_app"}})
    assert window.quitting and pc.setting("reopen_after_update") == "shown"
    argv: list[str] = []
    assert open_after_update(pc, argv) and argv == []
    # Only once: a later reopen with nothing remembered stays shut.
    assert not open_after_update(pc, argv)
    pc.set_setting("reopen_after_update", "hidden")
    assert open_after_update(pc, argv) and argv == ["--hidden"]


def test_an_update_on_another_pc_leaves_this_app_open(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    window.agent.place = "Jo-PC"
    window.feed_message({"type": "event", "event": {"type": "update_closing_app"}})
    assert not window.quitting and not pc.setting("reopen_after_update")


def test_only_web_links_are_opened(monkeypatch):
    """A link from the agent (maybe someone else's PC) never runs a program."""
    import webbrowser

    from desktop.pc import WindowsPC

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    for link in ("https://adoptium.net/", "C:\\Windows\\System32\\calc.exe", "file:///C:/x.exe"):
        WindowsPC().open_link(link)
    assert opened == ["https://adoptium.net/"]


def test_an_answer_about_a_server_no_longer_open_is_dropped(qapp, agent_http):
    """A slow answer for the server left behind must not fill the one now
    open (its Undo would show the other server's change)."""
    window = signed_in_on(qapp, agent_http, "Survival")
    got: list = []
    late = window.still_on("survival")(got.append)
    window.open_server("creative")
    late({"undo": [{"id": 1, "title": "Survival's change"}], "redo": []})
    assert got == []


def test_an_unchecked_player_count_is_shown_as_unknown(qapp, agent_http):
    window = signed_in_on(qapp, agent_http, "Survival")
    panel = window.server_screen.panel
    wait_for(qapp, lambda: "Online now" in panel.online_title.text())  # its own load is in
    panel.show_players({"state": "ONLINE"}, {"online_count": None, "max_players": 20})
    qapp.processEvents()
    assert panel.online_title.text() == "Online now (? of 20)"
    assert "Unknown" in shown_text(panel) and "Nobody" not in shown_text(panel)
