"""Bedrock 1.26.50+ (NetherNet): the join page check, and that the agent's
Bedrock ping asks it before falling back to RakNet."""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from agent.minecraft import nethernet
from agent.monitoring import answering

# The fields mc-monitor reads from a real server's /v1/join page.
JOIN = {
    "name": "My Server",
    "protocol": 900,
    "version": "1.26.60",
    "level": "Bedrock level",
    "players": 2,
    "maxPlayers": 10,
    "gameType": 0,
}


def serve(status: int, body: bytes) -> HTTPServer:
    class Page(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status if self.path == "/v1/join" else 404)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_join_page_fields_are_read_as_sent():
    found = nethernet.parse_join(json.dumps(JOIN))
    assert found["name"] == "My Server"
    assert found["version"] == "1.26.60"
    assert (found["players"], found["max_players"]) == (2, 10)
    assert found["edition"] is None  # not sent, so not guessed


def test_not_a_join_page_is_no_answer():
    assert nethernet.parse_join("") is None
    assert nethernet.parse_join("<html>hello</html>") is None
    assert nethernet.parse_join("[1, 2]") is None
    assert nethernet.parse_join(json.dumps({"name": "x"})) is None
    odd = nethernet.parse_join(json.dumps({**JOIN, "players": "2", "maxPlayers": True}))
    assert (odd["players"], odd["max_players"]) == (None, None)


def test_ping_asks_the_join_page():
    server = serve(200, json.dumps(JOIN).encode())
    try:
        found = nethernet.ping("127.0.0.1", server.server_port)
    finally:
        server.shutdown()
    assert found["version"] == "1.26.60" and found["players"] == 2


def test_ping_is_none_for_errors_closed_ports_and_other_programs():
    server = serve(500, b"oops")
    try:
        assert nethernet.ping("127.0.0.1", server.server_port) is None
    finally:
        server.shutdown()
    assert nethernet.ping("127.0.0.1", closed_port(), timeout=0.5) is None

    # Something that isn't a web server on the port.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(2)

    def junk() -> None:
        for _ in range(2):  # once for http, once for https
            conn, _ = listener.accept()
            with conn:
                conn.recv(1024)
                conn.sendall(b"\x00\x01garbage\r\n\r\n")

    threading.Thread(target=junk, daemon=True).start()
    try:
        assert nethernet.ping("127.0.0.1", listener.getsockname()[1], timeout=1.0) is None
    finally:
        listener.close()


def test_bedrock_ping_answers_from_a_nethernet_server():
    server = serve(200, json.dumps(JOIN).encode())
    try:
        found = answering.ping("raknet", "127.0.0.1", server.server_port)
    finally:
        server.shutdown()
    assert found["connected"] is True
    assert found["answer"]["version"] == "1.26.60"


def test_bedrock_ping_still_falls_back_to_raknet(monkeypatch):
    pong = {"status": "MCPE;Old;800;1.21.95;0;10", "version": "1.21.95"}
    monkeypatch.setattr(answering.raknet, "ping", lambda host, port, timeout: pong)
    found = answering.ping("raknet", "127.0.0.1", closed_port())
    assert found == {"connected": None, "answer": pong}
