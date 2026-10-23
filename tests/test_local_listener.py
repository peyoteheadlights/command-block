"""How the desktop app on the agent's own PC finds and reaches the agent:
the pairing file in the data folder, and the API on 127.0.0.1 next to the
Tailscale address, so the app keeps working when Tailscale is down."""

from __future__ import annotations

import json
import socket

from agent import pairing
from agent.main import listen_sockets


def test_the_agent_writes_its_pairing_file_when_it_starts(client, config):
    data = json.loads(pairing.pairing_file(config).read_text(encoding="utf-8"))
    assert data["port"] == config.network.port
    assert data["tls"] is config.tls_enabled
    assert "fingerprint" in data and "api_version" in data
    text = json.dumps(data).lower()
    for secret in ("password", "token", "hash"):
        assert secret not in text


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_non_loopback_address_also_listens_on_127_0_0_1():
    # 127.0.0.2 stands in for the Tailscale address: bindable, but not 127.0.0.1.
    port = _free_port()
    sockets = listen_sockets("127.0.0.2", port, address_ready=True)
    try:
        assert sorted(s.getsockname() for s in sockets) == [
            ("127.0.0.1", port),
            ("127.0.0.2", port),
        ]
    finally:
        for s in sockets:
            s.close()


def test_before_the_address_appears_only_127_0_0_1_listens():
    port = _free_port()
    sockets = listen_sockets("203.0.113.77", port, address_ready=False)
    try:
        assert [s.getsockname() for s in sockets] == [("127.0.0.1", port)]
    finally:
        for s in sockets:
            s.close()


def test_loopback_and_every_interface_get_one_socket():
    for host in ("127.0.0.1", "0.0.0.0"):
        port = _free_port()
        sockets = listen_sockets(host, port, address_ready=True)
        try:
            assert [s.getsockname() for s in sockets] == [(host, port)]
        finally:
            for s in sockets:
                s.close()
