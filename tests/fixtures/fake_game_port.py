"""A stand-in for a Minecraft server's game port, for Join protection's
tests: it answers a server list ping with a small status, and a login with
"Login Success" (packet 2) and then echoes every byte back, so a test can
time a round trip. It reads a PROXY protocol v2 header first when one
comes, and keeps what it saw in ``seen``. Plain sockets and threads, so it
also runs inside the fake server's own process (fake_server.py)."""

from __future__ import annotations

import json
import socket
import struct
import threading

PROXY_V2 = b"\r\n\r\n\x00\r\nQUIT\n"


def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def packet(packet_id: int, body: bytes) -> bytes:
    inner = varint(packet_id) + body
    return varint(len(inner)) + inner


def mc_string(text: str) -> bytes:
    data = text.encode()
    return varint(len(data)) + data


class Conn:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buf = b""

    def take(self, count: int) -> bytes:
        while len(self.buf) < count:
            data = self.sock.recv(65536)
            if not data:
                raise ConnectionError("closed")
            self.buf += data
        out, self.buf = self.buf[:count], self.buf[count:]
        return out

    def varint(self) -> int:
        value = 0
        for shift in range(0, 35, 7):
            byte = self.take(1)[0]
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
        raise ValueError("varint")

    def packet(self) -> tuple[int, bytes]:
        body = self.take(self.varint())
        pos, packet_id = 0, 0
        for shift in range(0, 35, 7):
            byte = body[pos]
            pos += 1
            packet_id |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
        return packet_id, body[pos:]


def read_str(body: bytes, pos: int) -> tuple[str, int]:
    length, shift = 0, 0
    while True:
        byte = body[pos]
        pos += 1
        length |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            break
    return body[pos : pos + length].decode(), pos + length


class FakeGamePort:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, motd: str = "A fake server"):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(64)
        self.port = self.sock.getsockname()[1]
        self.motd = motd
        self.seen: list[dict] = []
        self.lock = threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self) -> None:
        self.sock.close()

    def _accept(self) -> None:
        while True:
            try:
                sock, peer = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(sock, peer), daemon=True).start()

    def _serve(self, sock: socket.socket, peer) -> None:
        conn = Conn(sock)
        seen: dict = {"peer": peer[0], "proxy": None}
        try:
            first = conn.take(2)
            if first == PROXY_V2[:2]:
                rest = conn.take(14)
                body = conn.take(struct.unpack("!H", rest[12:14])[0])
                if rest[11] == 0x11:
                    seen["proxy"] = socket.inet_ntoa(body[0:4])
                elif rest[11] == 0x21:
                    seen["proxy"] = socket.inet_ntop(socket.AF_INET6, body[0:16])
            else:
                conn.buf = first + conn.buf
            self._game(conn, seen)
        except (ConnectionError, OSError, IndexError, ValueError):
            pass
        finally:
            sock.close()

    def _game(self, conn: Conn, seen: dict) -> None:
        _, body = conn.packet()
        pos = 0
        while body[pos] & 0x80:
            pos += 1
        _, pos = read_str(body, pos + 1)
        intent = body[pos + 2]
        if intent == 1:
            conn.packet()
            status = {
                "version": {"name": "1.21.1", "protocol": 767},
                "players": {"max": 20, "online": 3},
                "description": {"text": self.motd},
            }
            conn.sock.sendall(packet(0, mc_string(json.dumps(status))))
            _, ping = conn.packet()
            conn.sock.sendall(packet(1, ping))
            return
        _, start = conn.packet()
        seen["name"], _ = read_str(start, 0)
        with self.lock:
            self.seen.append(seen)
        conn.sock.sendall(packet(2, b"welcome"))
        if conn.buf:
            conn.sock.sendall(conn.buf)
            conn.buf = b""
        while True:
            data = conn.sock.recv(65536)
            if not data:
                return
            conn.sock.sendall(data)
