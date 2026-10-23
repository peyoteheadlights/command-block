"""First run's Tailscale step: install Tailscale, sign it in, then ask the
agent to move onto its Tailscale address so phones can reach this PC.

The step only ever shows the PC as reachable once the agent says Tailscale
is connected and it listens there (house rule 1). The agent is asked to move
before the sign-in page opens, so closing the app half way doesn't matter:
the agent finishes the move by itself (agent/tailscale_watch.py).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from .agent import AgentError
from .ui import label, push
from .words import text

if TYPE_CHECKING:
    from .window import MainWindow

POLL_MS = 3000
GIVE_UP_AFTER = 10 * 60  # seconds without Tailscale connecting: "didn't finish"


class QrCode(QWidget):
    """The pairing link as a QR code, drawn from the agent's rows of 0s and 1s."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[str] = []
        self.setFixedSize(220, 220)

    def show_rows(self, rows: list[str]) -> None:
        self.rows = rows
        self.update()

    def paintEvent(self, _event) -> None:
        if not self.rows:
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("white"))  # a QR code needs its light border
        cell = self.width() / (len(self.rows) + 8)
        for y, row in enumerate(self.rows):
            for x, dark in enumerate(row):
                if dark == "1":
                    painter.fillRect(
                        QRectF((x + 4) * cell, (y + 4) * cell, cell + 0.5, cell + 0.5),
                        QColor("black"),
                    )


class TailscaleStep(QWidget):
    NAME = "Tailscale"

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        self.sign_in_page: str | None = None
        self.waiting_since = 0.0
        self.restarting = False
        self.first: Callable[[], None] | None = None  # done instead of load() on showing
        sheet = QFrame()
        sheet.setObjectName("sheet")
        sheet.setFixedWidth(520)
        column = QVBoxLayout(sheet)
        column.setContentsMargins(28, 24, 28, 24)
        column.setSpacing(10)
        heading = label(text("desktop.tailscale.title"))
        heading.setObjectName("title")
        column.addWidget(heading)
        self.words = label()
        column.addWidget(self.words)
        self.problem = label(kind="problem")
        column.addWidget(self.problem)
        self.qr = QrCode()
        self.qr.setAccessibleName(text("desktop.tailscale.qr"))
        column.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignHCenter)
        self.qr_words = label(kind="sub")
        column.addWidget(self.qr_words)
        buttons = QHBoxLayout()
        self.main = push("", "primary")
        self.main_action: Callable[[], None] = self.load
        self.main.clicked.connect(lambda: self.main_action())
        self.other = push(text("desktop.tailscale.other_account"))
        self.other.clicked.connect(self.different_account)
        self.skip = push(text("desktop.tailscale.skip"))
        self.skip.clicked.connect(self.skip_for_now)
        for button in (self.main, self.other, self.skip):
            buttons.addWidget(button)
        buttons.addStretch()
        column.addLayout(buttons)
        outer = QVBoxLayout(self)
        outer.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(sheet)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()
        # Asks the agent while the person signs in, only while this shows.
        self.poll = QTimer(self)
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(self.load)

    def say(
        self,
        words: str,
        button: str = "",
        action: Callable[[], None] | None = None,
        problem: str = "",
    ) -> None:
        """One state of the step. Every state but "done" can be skipped."""
        self.words.setText(words)
        self.problem.setText(problem)
        self.problem.setVisible(bool(problem))
        self.main.setText(button)
        self.main.setVisible(bool(button))
        self.main_action = action or self.load
        self.other.hide()
        self.qr.hide()
        self.qr_words.hide()
        self.skip.show()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        first, self.first = self.first, None
        (first or self.load)()

    def open_with(self, first: Callable[[], None]) -> None:
        """Shows the step doing ``first`` (PC settings' "Use a different account")."""
        self.first = first
        self.window_.show_screen("Tailscale")

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.poll.stop()

    def load(self) -> None:
        window = self.window_
        if window.agent.place != "this PC":
            self.say(text("desktop.tailscale.not_here", pc=window.agent.place))
            return
        window.run(window.agent.tailscale, self.show_state, self.failed)

    def failed(self, exc: AgentError) -> None:
        self.poll.stop()
        self.say(
            text("desktop.tailscale.intro"),
            text("desktop.tailscale.retry"),
            self.load,
            problem=str(exc),
        )

    def show_state(self, state: dict) -> None:
        error = state.get("error") or ""
        if state.get("restarting"):
            self.poll.stop()
            self.restarting = True
            self.say(text("desktop.tailscale.restarting"))
            self.skip.hide()
        elif state.get("connected") and state.get("moved") and not state.get("moving"):
            self.done(state)
        elif state.get("connected"):
            self.window_.run(self.window_.agent.tailscale_move, self.show_state, self.failed)
        elif "in use by" in error.lower() or "another user" in error.lower():
            # Someone else's Tailscale: never install, sign in or sign out over it.
            self.poll.stop()
            self.say(text("desktop.tailscale.other_user", why=error))
        elif self.poll.isActive():
            if time.monotonic() - self.waiting_since > GIVE_UP_AFTER:
                self.failed(AgentError(text("desktop.tailscale.unfinished")))
        elif not state.get("installed"):
            self.say(
                text("desktop.tailscale.intro"), text("desktop.tailscale.install"), self.install
            )
        else:
            self.say(
                text("desktop.tailscale.sign_in_words"),
                text("desktop.tailscale.sign_in"),
                self.sign_in,
            )

    def install(self) -> None:
        self.say(text("desktop.tailscale.downloading"))
        self.window_.run(self.window_.pc.install_tailscale, lambda _r: self.sign_in(), self.failed)

    def sign_in(self) -> None:
        """``tailscale up``, after asking the agent to move once it connects."""
        self.say(text("desktop.tailscale.opening"))
        window = self.window_

        def work() -> str | None:
            window.agent.tailscale_move()
            return window.pc.tailscale_up()

        window.run(work, self.signing_in, self.failed)

    def signing_in(self, page: str | None) -> None:
        self.sign_in_page = page
        if page:
            self.window_.pc.open_link(page)
            self.say(
                text("desktop.tailscale.waiting"),
                text("desktop.tailscale.open_again"),
                lambda: self.window_.pc.open_link(page),
            )
        self.waiting_since = time.monotonic()
        self.poll.start()
        self.load()

    def done(self, state: dict) -> None:
        self.poll.stop()
        self.restarting = False
        account = state.get("account")
        self.say(
            text("desktop.tailscale.done", account=account)
            if account
            else text("desktop.tailscale.done_no_account"),
            text("desktop.tailscale.finish"),
            self.finish,
        )
        self.skip.hide()
        self.other.show()
        self.window_.run(self.window_.agent.pairing, self.show_pairing, lambda _e: None)
        self.window_.tailscale_loaded(state)

    def show_pairing(self, found: dict) -> None:
        if found.get("ok") and found.get("qr"):
            self.qr.show_rows(found["qr"])
            self.qr.show()
            self.qr_words.setText(text("desktop.tailscale.qr"))
        else:
            self.qr_words.setText(found.get("reason") or "")
        self.qr_words.show()

    def different_account(self) -> None:
        """Signs this PC's Tailscale out, then in again; the agent moves to
        the new address and the new pairing QR code shows."""
        self.say(text("desktop.tailscale.opening"))
        window = self.window_

        def work() -> None:
            window.agent.tailscale_move()  # first, so signing out isn't an alert
            window.pc.tailscale_logout()

        window.run(work, lambda _r: self.sign_in(), self.failed)

    def skip_for_now(self) -> None:
        self.poll.stop()
        window = self.window_
        window.run(window.agent.tailscale_skip, window.tailscale_skipped, self.failed)

    def finish(self) -> None:
        self.window_.show_screen("All servers")
