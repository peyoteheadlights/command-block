"""Connecting to someone else's PC (a moderator's PC has no agent of its own).

Tailscale comes first: when it isn't connected on this PC, the window
installs it (Tailscale's own signed setup, after a click) and signs it in.
Then the moderator pastes the Tailscale share link the owner sent (it opens
in the browser to be accepted), and the link the owner gave them: the
pairing link, or an invite, which is the same link plus a one-time invite.
The link holds the PC's address, port and certificate fingerprint, never a
password, and the app checks the certificate before it sends anything
(AgentClient.reach).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from . import theme
from .agent import AgentClient, AgentError
from .pc import WindowsPC
from .ui import label, push
from .window import Task
from .words import text

SHARE_HOST = "login.tailscale.com"
POLL_MS = 3000  # asks Tailscale while the person signs in


def read_link(link: str) -> dict[str, Any]:
    """The parts of a pairing or invite link, or AgentError in plain words."""
    parsed = urlparse(link.strip())
    query = parse_qs(parsed.query)
    fingerprint = (query.get("fp") or [""])[0].lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not port
        or query.get("pair") != ["1"]
        or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
    ):
        raise AgentError(text("desktop.connect.not_a_link"))
    return {
        "host": parsed.hostname,
        "port": port,
        "fingerprint": fingerprint,
        "invite": (query.get("invite") or [""])[0] or None,
        "user": (query.get("user") or [""])[0] or None,
        # What's kept to connect next time: the link without the invite.
        "pc_link": parsed._replace(
            query=urlencode({"pair": 1, "fp": fingerprint, "api": (query.get("api") or [""])[0]})
        ).geturl(),
    }


def is_share_link(link: str) -> bool:
    """A Tailscale share link (https://login.tailscale.com/...)."""
    parsed = urlparse(link.strip())
    return parsed.scheme == "https" and parsed.hostname == SHARE_HOST


def agent_for(found: dict[str, Any]) -> AgentClient:
    return AgentClient.connect(found["host"], found["port"], found["fingerprint"])


class ConnectWindow(QWidget):
    """Paste a link; with an invite, choose a password too. ``connected`` is
    called with the agent once the PC answered with the right certificate."""

    def __init__(self, pc: WindowsPC, connected: Callable[[AgentClient], None]) -> None:
        super().__init__()
        self.pc, self.connected = pc, connected
        self.agent: AgentClient | None = None
        self.found: dict[str, Any] = {}
        self._tasks: set[Task] = set()
        self.setWindowTitle(text("app.name"))
        self.setObjectName("desk")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        self.setStyleSheet(theme.style_sheet(theme.tokens(dark, None), pc.text_scale()))
        self.resize(720, 520)
        sheet = QFrame()
        sheet.setObjectName("sheet")
        sheet.setFixedWidth(460)
        form = QVBoxLayout(sheet)
        form.setContentsMargins(28, 24, 28, 24)
        form.setSpacing(10)
        self.heading = label(text("desktop.connect.title"))
        self.heading.setObjectName("title")
        self.words = label(text("desktop.connect.words"))
        self.link = QLineEdit()
        self.link.setObjectName("link")
        self.link.setPlaceholderText(text("desktop.connect.link"))
        self.link.setAccessibleName(text("desktop.connect.link"))
        self.link.returnPressed.connect(self.go)
        self.username = QLineEdit()
        self.username.setObjectName("username")
        self.username.setReadOnly(True)
        self.username.setAccessibleName(text("login.username"))
        self.password = QLineEdit()
        self.password.setObjectName("password")
        self.again = QLineEdit()
        self.again.setObjectName("again")
        for field, words in (
            (self.password, "login.password"),
            (self.again, "desktop.connect.again"),
        ):
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText(text(words))
            field.setAccessibleName(text(words))
        self.again.returnPressed.connect(self.go)
        self.problem = label(kind="problem")
        self.problem.hide()
        self.button = push(text("desktop.connect.connect"), "primary")
        self.action: Callable[[], None] = self.go
        self.button.clicked.connect(lambda: self.action())
        self.skip = push(text("desktop.tailscale.skip"))
        self.skip.clicked.connect(self.skip_tailscale)
        self.skip.hide()
        for widget in (
            self.heading,
            self.words,
            self.link,
            self.username,
            self.password,
            self.again,
            self.problem,
        ):
            form.addWidget(widget)
        buttons = QHBoxLayout()
        buttons.addWidget(self.button)
        buttons.addWidget(self.skip)
        buttons.addStretch()
        form.addLayout(buttons)
        for field in (self.username, self.password, self.again):
            field.hide()
        outer = QVBoxLayout(self)
        outer.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(sheet)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()
        self.poll = QTimer(self)
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(self.check_tailscale)
        self.words.setText(text("desktop.connect.checking"))
        self.link.hide()
        self.check_tailscale()

    # ------------------------------------------------------------ Tailscale first
    def check_tailscale(self) -> None:
        self.run(self.pc.tailscale_state, self.tailscale_state, wait=False)

    def tailscale_state(self, state: dict) -> None:
        error = (state.get("error") or "").lower()
        if "in use by" in error or "another user" in error:
            # Someone else's Tailscale: never install, sign in or sign out over it.
            self.ask_for_link()
            self.say(text("desktop.tailscale.other_user", why=state["error"]))
        elif state.get("connected"):
            if self.agent is None:  # not once choosing the invite's password
                self.ask_for_link()
        elif not self.poll.isActive() and not self.link.isVisible():
            # While signing in it moves on by itself, and after Skip it stays.
            installed = state.get("cli_found")
            self.show_step(
                text("desktop.connect.tailscale_title"),
                text(
                    "desktop.tailscale.sign_in_words" if installed else "desktop.connect.tailscale"
                ),
                text("desktop.tailscale.sign_in" if installed else "desktop.tailscale.install"),
                self.sign_in if installed else self.install,
            )

    def show_step(self, heading: str, words: str, button: str, action: Callable[[], None]) -> None:
        self.heading.setText(heading)
        self.words.setText(words)
        self.button.setText(button)
        self.action = action
        self.link.hide()
        self.skip.show()

    def install(self) -> None:
        self.words.setText(text("desktop.tailscale.downloading"))
        self.run(self.pc.install_tailscale, lambda _r: self.sign_in())

    def sign_in(self) -> None:
        self.words.setText(text("desktop.tailscale.opening"))
        self.run(self.pc.tailscale_up, self.signing_in)

    def signing_in(self, page: str | None) -> None:
        if page:
            self.pc.open_link(page)
            self.show_step(
                text("desktop.connect.tailscale_title"),
                text("desktop.tailscale.waiting"),
                text("desktop.tailscale.open_again"),
                lambda: self.pc.open_link(page),
            )
        self.poll.start()
        self.check_tailscale()

    def skip_tailscale(self) -> None:
        self.say("")
        self.ask_for_link()

    def ask_for_link(self) -> None:
        self.poll.stop()
        self.heading.setText(text("desktop.connect.title"))
        self.words.setText(text("desktop.connect.words"))
        self.button.setText(text("desktop.connect.connect"))
        self.action = self.go
        self.skip.hide()
        self.link.show()
        self.link.setFocus()

    def say(self, problem: str) -> None:
        self.problem.setText(problem)
        self.problem.setVisible(bool(problem))

    def run(self, work: Callable[[], Any], done: Callable[[Any], None], wait: bool = True) -> None:
        """``wait``: the button waits for it (False: checking Tailscale)."""
        self.button.setEnabled(not wait)
        task = Task(work)
        self._tasks.add(task)

        def finish() -> None:
            self._tasks.discard(task)
            if wait:
                self.button.setEnabled(True)

        def on_done(result: Any) -> None:
            finish()
            done(result)

        def on_failed(exc: AgentError) -> None:
            finish()
            self.say(str(exc))

        task.signals.done.connect(on_done)
        task.signals.failed.connect(on_failed)
        QThreadPool.globalInstance().start(task)

    def go(self) -> None:
        self.say("")
        if self.agent is not None and self.found.get("invite"):
            self.join()
            return
        if is_share_link(self.link.text()):
            # The owner's Tailscale share: accepted in the browser, then the
            # pairing link or invite goes in the same box.
            self.pc.open_link(self.link.text().strip())
            self.link.clear()
            self.words.setText(text("desktop.connect.share_opened"))
            return
        try:
            found = read_link(self.link.text())
        except AgentError as exc:
            self.say(str(exc))
            return

        def work() -> AgentClient:
            agent = agent_for(found)
            agent.health()  # reaches it and checks the certificate first
            if found["invite"]:
                found["user"] = agent.check_invite(found["invite"])["username"]
            return agent

        def reached(agent: AgentClient) -> None:
            self.found = found
            if found["invite"]:
                self.agent = agent
                self.choose_password()
            else:
                self.done(agent)

        self.run(work, reached)

    def choose_password(self) -> None:
        self.heading.setText(text("desktop.connect.join_title"))
        self.words.setText(text("desktop.connect.join_words", pc=self.found["host"]))
        self.link.hide()
        self.username.setText(self.found["user"] or "")
        for field in (self.username, self.password, self.again):
            field.show()
        self.button.setText(text("desktop.connect.join"))
        self.password.setFocus()

    def join(self) -> None:
        if self.password.text() != self.again.text():
            self.say(text("desktop.connect.no_match"))
            return
        agent, invite, password = self.agent, self.found["invite"], self.password.text()
        assert agent is not None
        device = self.pc.device_name()

        def work() -> AgentClient:
            agent.accept_invite(invite, password, device)
            return agent

        def joined(agent: AgentClient) -> None:
            assert agent.token is not None
            self.pc.save_sign_in(agent.place, agent.token)  # kept until signed out
            self.done(agent)

        self.run(work, joined)

    def done(self, agent: AgentClient) -> None:
        self.pc.set_setting("pc_link", self.found["pc_link"])
        self.close()
        self.connected(agent)
