"""The Diagnose server window: starts a run and shows its list as it grows.

The agent does the work (agent/diagnose.py) and keeps the list; this window
asks for it once a second while the run lasts, then stops asking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
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
        self.start()

    def start(self) -> None:
        self.again.hide()
        self.status.setText(text("diagnose.starting"))
        agent, sid = self.window_.agent, self.server_id
        self.window_.run(
            lambda: agent.call("POST", f"/servers/{sid}/server/diagnose", {}),
            lambda _job: self.timer.start(),
            self.failed,
        )

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
        super().closeEvent(event)
