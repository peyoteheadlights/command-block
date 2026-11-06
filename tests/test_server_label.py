"""One answer to "what does the server show, and what can be pressed",
worked out on the agent so every app says the same thing."""

import pytest

from agent.minecraft.state import ServerState, controls

from .test_api import auth, token_for

S = ServerState


@pytest.mark.parametrize(
    ("state", "label", "can"),
    [
        (S.OFFLINE, "offline", ["start", "change"]),
        (S.CRASHED, "crashed", ["start", "change"]),
        (S.STARTING, "starting", ["stop"]),
        (S.ONLINE, "online", ["stop", "restart"]),
        (S.STOPPING, "stopping", []),
        (S.RESTARTING, "restarting", []),
        # The agent starts it itself: Start would be refused.
        (S.RESTART_PENDING, "crashed", []),
        # Not looked yet: starting now could start a second java.
        (S.UNKNOWN, "unknown", []),
    ],
)
def test_each_state_has_one_label_and_one_list(state, label, can):
    assert controls(state) == {"label": label, "can": can}


def test_a_stuck_server_reads_not_answering_while_its_problem_is_kept():
    # During a backup the check is skipped (ok is None) but the problem stays.
    stuck = {"ok": None, "problem": {"what": "no answer"}}
    assert controls(S.ONLINE, answering=stuck)["label"] == "not_answering"
    assert controls(S.ONLINE, answering={"ok": True, "problem": None})["label"] == "online"
    # Only an online server can be "not answering".
    assert controls(S.STOPPING, answering=stuck)["label"] == "stopping"
    # Restart is how it gets fixed, so it stays.
    assert "restart" in controls(S.ONLINE, answering=stuck)["can"]


def test_a_busy_server_can_only_be_stopped():
    assert controls(S.OFFLINE, busy="Restoring a backup")["can"] == []
    assert controls(S.ONLINE, busy="Backing up")["can"] == ["stop"]


def test_a_server_without_its_console_can_only_be_force_stopped():
    assert controls(S.ONLINE, left_behind=True) == {"label": "online", "can": ["force_stop"]}


def test_status_and_servers_send_the_same_label_and_list(client):
    headers = auth(token_for(client))
    ctx = next(iter(client.app.state.core.servers.values()))

    def both():
        status = client.get("/api/status", headers=headers).json()
        row = client.get("/api/servers", headers=headers).json()["servers"][0]
        return status, row

    status, row = both()
    assert status["label"] == row["label"] == "offline"
    assert status["can"] == row["can"] == ["start", "change"]
    ctx.answering.problem = {"what": "no answer", "why": [], "since": 1, "fix": ""}
    ctx.server.state = S.ONLINE
    status, row = both()
    assert status["label"] == row["label"] == "not_answering"
    ctx.server.state = S.OFFLINE


def test_the_desktop_sends_force_stop_as_a_forced_stop():
    from desktop.agent import AgentClient

    sent = []
    agent = AgentClient.__new__(AgentClient)
    agent.call = lambda *args: sent.append(args)  # type: ignore[method-assign]
    agent.server_action("survival", "force_stop")
    agent.server_action("survival", "stop")
    assert sent == [
        ("POST", "/servers/survival/server/stop", {"force": True}),
        ("POST", "/servers/survival/server/stop", {}),
    ]


def test_the_mods_list_says_when_mods_may_change(client):
    headers = auth(token_for(client))
    mods = client.get("/api/mods", headers=headers).json()
    assert mods["label"] == "offline" and "change" in mods["can"]
