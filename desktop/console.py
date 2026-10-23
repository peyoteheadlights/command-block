"""The Console page (Technical only): the server's live output and a box to
type Minecraft commands. Lines arrive on the live feed only while the page
is open; the agent keeps the rest, so "Load more" can fetch them."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from urllib.parse import quote

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton

from .agent import AgentError
from .ui import Page, label, push
from .words import text

if TYPE_CHECKING:
    from .window import MainWindow

KEPT = 1000  # lines the page holds


def words(key: str, **values) -> str:
    return text(key, technical=True, **values)


class ConsolePage(Page):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.lines: list[str] = []
        tools = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setObjectName("consoleFilter")
        self.filter.setPlaceholderText(words("console.filter"))
        self.filter.setAccessibleName(words("console.filter_label"))
        self.filter.textChanged.connect(self.show_lines)
        tools.addWidget(self.filter, 1)
        self.follow = self.toggle("console.follow", "console.follow_title", True)
        self.pause = self.toggle("console.pause", "console.pause_title", False)
        self.pause.toggled.connect(self.paused)
        tools.addWidget(self.follow)
        tools.addWidget(self.pause)
        for key, tip, do in (
            ("console.copy", "console.copy_title", self.copy),
            ("desktop.console.save", "console.download_title", self.save),
            ("console.clear", "console.clear_title", self.clear),
        ):
            button = push(words(key))
            button.setToolTip(words(tip))
            button.clicked.connect(do)
            tools.addWidget(button)
            if key == "console.clear":
                self.clear_button = button
        self.column.addLayout(tools)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(KEPT)
        self.log.setAccessibleName(words("console.output_label"))
        self.column.addWidget(self.log, 1)
        send_row = QHBoxLayout()
        send_row.addWidget(label(">", "address"))
        self.command = QLineEdit()
        self.command.setObjectName("consoleCommand")
        self.command.setPlaceholderText(words("console.command_placeholder"))
        self.command.setAccessibleName(words("console.command_label"))
        self.command.returnPressed.connect(self.send)
        send_row.addWidget(self.command, 1)
        self.send_button = push(words("console.send"), "primary")
        self.send_button.clicked.connect(self.send)
        send_row.addWidget(self.send_button)
        self.column.addLayout(send_row)
        hint = QHBoxLayout()
        self.hint = label(words("console.hint"), "sub")
        hint.addWidget(self.hint, 1)
        more = push(words("console.load_more"), "plain")
        more.clicked.connect(lambda: self.ask("GET", "/logs?lines=500", self.show_log))
        hint.addWidget(more)
        self.column.addLayout(hint)

    def toggle(self, key: str, tip: str, on: bool) -> QPushButton:
        button = push(words(key))
        button.setCheckable(True)
        button.setChecked(on)
        button.setToolTip(words(tip))
        button.setProperty("kind", "segment")
        return button

    def paused(self, on: bool) -> None:
        if not on:
            self.show_lines()  # what came while paused

    # ------------------------------------------------------------ loading
    def load(self) -> None:
        typing = self.window_.may(self.row["id"], "console.send")
        for widget in (self.command, self.send_button, self.clear_button, self.hint):
            widget.setVisible(typing)
        self.ask("GET", "/logs?lines=200", self.show_log)
        self.command.setFocus()

    def changed(self) -> None:
        pass  # new lines come on the live feed

    def show_log(self, data: dict) -> None:
        history = [line["raw"] for line in data.get("lines") or []]
        # Lines the live feed brought while this was loading are kept.
        self.lines = (history + [line for line in self.lines if line not in history])[-KEPT:]
        self.show_lines()

    def shown(self) -> list[str]:
        needle = self.filter.text().lower()
        return [line for line in self.lines if needle in line.lower()] if needle else self.lines

    def show_lines(self) -> None:
        lines = self.shown()
        self.log.setPlainText("\n".join(lines))
        if not lines:
            needle = self.filter.text()
            self.log.setPlaceholderText(
                words("console.no_match_hint", filter=needle)
                if needle
                else f"{words('console.empty')}. {words('console.empty_hint')}"
            )
        self.to_bottom()

    def to_bottom(self) -> None:
        if self.follow.isChecked():
            bar = self.log.verticalScrollBar()
            bar.setValue(bar.maximum())

    def live(self, event: dict) -> None:
        if event.get("type") != "console":
            return
        raw = (event.get("data") or {}).get("raw") or event.get("message") or ""
        self.lines.append(raw)
        del self.lines[:-KEPT]
        needle = self.filter.text().lower()
        if self.pause.isChecked() or (needle and needle not in raw.lower()):
            return
        self.log.appendPlainText(raw)
        self.to_bottom()

    # ------------------------------------------------------------ the buttons
    def copy(self) -> None:
        QGuiApplication.clipboard().setText("\n".join(self.shown()))
        self.say(words("console.copied"))

    def save(self) -> None:
        name = f"console-{time.strftime('%Y-%m-%d-%H-%M-%S')}.log"
        try:
            path = self.window_.pc.save_file(name, "\n".join(self.shown()).encode("utf-8"))
        except AgentError as exc:
            self.say(str(exc))
            return
        if path:
            self.say(text("desktop.saved", path=path))

    def clear(self) -> None:
        def done(_result) -> None:
            self.lines = []
            self.show_lines()
            self.say(words("console.cleared"))

        self.ask("POST", "/logs/clear", done)

    def send(self) -> None:
        command = self.command.text().strip()
        if not command:
            return
        self.ask(
            "GET",
            "/server/command/check?command=" + quote(command, safe=""),
            lambda check: self.checked(command, check),
        )

    def checked(self, command: str, check: dict) -> None:
        if not check.get("valid") and check.get("error") and not check.get("danger_reason"):
            self.say(check["error"])
            return
        if check.get("danger_reason") and not self.window_.confirm(
            words("console.run_title", command=command),
            check["danger_reason"],
            words("console.run"),
        ):
            return
        self.send_button.setEnabled(False)

        def done(_result) -> None:
            self.command.clear()
            self.say("")

        self.ask(
            "POST",
            "/server/command",
            done,
            lambda exc: self.say(str(exc)),
            body={"command": command, "confirm": True},
            always=lambda: self.send_button.setEnabled(True),
        )
