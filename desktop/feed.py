"""The live feed: one websocket to the agent that keeps the window current.

It runs on its own thread and hands each message to the window through a Qt
signal. When the agent goes away (restarting, the PC asleep) it tries again,
waiting a little longer each time, up to 30 seconds.
"""

from __future__ import annotations

import json
import threading

from PySide6.QtCore import QObject, Signal
from websockets.exceptions import WebSocketException

from .agent import AgentClient, AgentError


class Feed(QObject):
    message = Signal(dict)
    lost = Signal()  # it couldn't connect, or the connection dropped

    def __init__(self, agent: AgentClient) -> None:
        super().__init__()
        self.agent = agent
        self.connected = False
        # Each run has its own stop flag, so signing out and straight back in
        # never leaves the old run going or the new one stopped.
        self._stop = threading.Event()
        self._stop.set()

    def start(self) -> None:
        if not self._stop.is_set():
            return
        self._stop = threading.Event()
        threading.Thread(
            target=self._run, args=(self._stop,), name="live feed", daemon=True
        ).start()

    def stop(self) -> None:
        self._stop.set()
        self.connected = False

    def _run(self, stop: threading.Event) -> None:
        wait = 1.0
        while not stop.is_set():
            try:
                with self.agent.open_feed() as ws:
                    while not stop.is_set():
                        try:
                            # A second at a time, so stop() is noticed.
                            message = json.loads(ws.recv(timeout=1))
                        except TimeoutError:
                            continue
                        if stop.is_set():
                            return
                        if message.get("type") == "ready":
                            self.connected, wait = True, 1.0
                        self.message.emit(message)
                        if message.get("type") == "error":
                            return  # the sign-in ended; the window says so
            except (AgentError, OSError, WebSocketException, ValueError):
                pass
            self.connected = False
            if not stop.is_set():
                self.lost.emit()
            stop.wait(wait)
            wait = min(wait * 2, 30.0)
