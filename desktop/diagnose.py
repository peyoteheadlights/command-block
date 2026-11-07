"""The Diagnose server window: starts a run and shows its list as it grows.

The agent does the work (agent/diagnose.py) and keeps the list; this window
asks for it once a second while the run lasts, then stops asking.

At the top: the owner's Microsoft account, which online-mode and Bedrock
joins use, with Sign in with Microsoft (a code to type on Microsoft's page,
agent/microsoft.py) or Sign out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

from .agent import AgentError
from .ui import label, push
from .words import text

if TYPE_CHECKING:
    from .window import MainWindow

MARKS = {True: "✓", False: "✗", None: "?"}


class DiagnoseWindow(QDialog):
    def __init__(self, window: MainWindow, server_id: str, name: str) -> None:
        super().__init__(window)
        self.window_ = window
        self.server_id = server_id
        self.setWindowTitle(text("diagnose.title", name=name))
        self.setMinimumWidth(520)
        column = QVBoxLayout(self)
        account = QHBoxLayout()
        self.account = label("", "sub")
        self.account.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        account.addWidget(self.account, 1)
        self.open_page = push(text("microsoft.open_page"), "plain")
        self.open_page.clicked.connect(self.open_microsoft)
        self.copy_code = push(text("microsoft.copy_code"), "plain")
        self.copy_code.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.code or ""))
        self.sign_in = push(text("microsoft.sign_in"))
        self.sign_in.clicked.connect(self.start_sign_in)
        self.sign_out = push(text("microsoft.sign_out"), "plain")
        self.sign_out.clicked.connect(self.do_sign_out)
        for button in (self.open_page, self.copy_code, self.sign_in, self.sign_out):
            button.hide()
            account.addWidget(button)
        column.addLayout(account)
        self.code: str | None = None
        self.url = ""
        self.signing = QTimer(self)
        self.signing.setInterval(2000)
        self.signing.timeout.connect(self.ask_account)
        self.status = label(text("diagnose.starting"), "sub")
        column.addWidget(self.status)
        self.list = QVBoxLayout()
        column.addLayout(self.list)
        column.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        self.again = push(text("diagnose.again"))
        self.again.clicked.connect(self.start)
        self.again.hide()
        row.addWidget(self.again)
        close = push(text("action.close"), "plain")
        close.clicked.connect(self.close)
        row.addWidget(close)
        column.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.ask)
        self.ask_account()
        self.start()

    def start(self, go_ahead: bool = False) -> None:
        self.again.hide()
        self.status.setText(text("diagnose.starting"))
        agent, sid = self.window_.agent, self.server_id
        self.window_.run(
            lambda: agent.call("POST", f"/servers/{sid}/server/diagnose", {"go_ahead": go_ahead}),
            self.started,
            self.failed,
        )

    def started(self, answer: dict) -> None:
        if not answer.get("ask"):
            self.timer.start()
            return
        # The owner is playing, and a join with their account kicks them out.
        if self.window_.confirm(text("action.diagnose"), answer["ask"], text("diagnose.go_ahead")):
            self.start(go_ahead=True)
        else:
            self.status.setText(text("diagnose.not_started"))
            self.again.show()

    # ---------------------------------------------------- Microsoft account
    def ask_account(self) -> None:
        agent = self.window_.agent
        self.window_.run(
            lambda: agent.call("GET", "/microsoft"), self.show_account, self.no_account
        )

    def no_account(self, _exc: AgentError) -> None:
        self.signing.stop()
        for widget in (self.account, self.open_page, self.copy_code, self.sign_in, self.sign_out):
            widget.hide()

    def show_account(self, status: dict) -> None:
        self.code, self.url = status.get("code"), status.get("url") or ""
        waiting = bool(self.code) and not status.get("signed_in")
        if status.get("signed_in"):
            names = [n for n in (status.get("java_name"), status.get("gamertag")) if n]
            words = text("microsoft.signed_in", names=", ".join(names) or "?")
        elif waiting:
            words = text("microsoft.type_code", url=self.url, code=self.code)
        else:
            words = status.get("error") or text("microsoft.signed_out")
        self.account.setText(words)
        self.account.show()
        self.sign_out.setVisible(bool(status.get("signed_in")))
        self.sign_in.setVisible(not status.get("signed_in") and not waiting)
        self.open_page.setVisible(waiting)
        self.copy_code.setVisible(waiting)
        if waiting:
            self.signing.start()
        else:
            if self.signing.isActive() and status.get("signed_in"):
                self.start()  # the joins that waited for the sign-in
            self.signing.stop()

    def start_sign_in(self) -> None:
        self.sign_in.setEnabled(False)
        self.account.setText(text("microsoft.asking"))
        agent = self.window_.agent
        self.window_.run(
            lambda: agent.call("POST", "/microsoft/sign-in", {}),
            self.show_account,
            lambda exc: self.account.setText(str(exc)),
            lambda: self.sign_in.setEnabled(True),
        )

    def open_microsoft(self) -> None:
        if self.url.startswith("https://"):
            QDesktopServices.openUrl(QUrl(self.url))

    def do_sign_out(self) -> None:
        agent = self.window_.agent
        self.window_.run(lambda: agent.call("POST", "/microsoft/sign-out", {}), self.show_account)

    def ask(self) -> None:
        agent, sid = self.window_.agent, self.server_id
        self.window_.run(
            lambda: agent.call("GET", f"/servers/{sid}/server/diagnose"), self.show_result
        )

    def failed(self, exc: AgentError) -> None:
        self.timer.stop()
        self.status.setText(str(exc))
        self.again.show()

    def show_result(self, result: dict | None) -> None:
        if not result:
            return
        while self.list.count():
            old = self.list.takeAt(0).widget()
            if old is not None:
                old.setParent(None)
        group = None
        for line in result.get("lines") or []:
            if line["group"] != group:
                group = line["group"]
                self.add(label(group, name="h2"))
            words = f"{MARKS[line['ok']]}  {line['what']}"
            if line.get("detail"):
                words += f"\n     {line['detail']}"
            kind = "problem" if line["ok"] is False else ("muted" if line["ok"] is None else "")
            self.add(label(words, kind))
        if result.get("running"):
            self.status.setText(text("diagnose.running"))
            return
        self.timer.stop()
        self.status.setText(text("diagnose.done"))
        self.again.show()

    def add(self, widget: QLabel) -> None:
        self.list.addWidget(widget)
        widget.show()  # added after the window opened: Qt keeps it hidden otherwise

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.signing.stop()
        super().closeEvent(event)
