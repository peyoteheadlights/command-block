"""The Console page (Technical only): the server's live output and a box to
type Minecraft commands. Lines arrive on the live feed only while the page
is open; the agent keeps the rest, so "Load more" can fetch them."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING
from urllib.parse import quote

from PySide6.QtCore import QEvent, QStringListModel, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import streamer
from .agent import AgentError
from .ui import Page, label, push, refill
from .words import text

if TYPE_CHECKING:
    from .window import MainWindow

KEPT = 1000  # lines the page holds
HISTORY = 50  # commands remembered per server, on this PC


class CommandLine(QLineEdit):
    """The command box: Up and Down bring back earlier commands, Tab takes
    the completion, Escape empties it."""

    def __init__(self) -> None:
        super().__init__()
        self.history: list[str] = []
        self.at = 0  # where Up and Down are in history; len(history) is "new"
        self.draft = ""

    def remember(self, command: str) -> None:
        if command in self.history:
            self.history.remove(command)
        self.history = [*self.history, command][-HISTORY:]
        self.at = len(self.history)

    def event(self, event) -> bool:
        # Tab would move to the next button before keyPressEvent sees it.
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Tab:
            completer = self.completer()
            if completer and completer.completionCount():
                completer.setCompletionPrefix(self.text())
                self.setText(completer.currentCompletion() + " ")
                completer.popup().hide()
                return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        popup = self.completer().popup() if self.completer() else None
        if popup is not None and popup.isVisible():
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.history:
            if self.at == len(self.history):
                self.draft = self.text()
            step = -1 if key == Qt.Key.Key_Up else 1
            self.at = max(0, min(len(self.history), self.at + step))
            self.setText(self.history[self.at] if self.at < len(self.history) else self.draft)
            return
        if key == Qt.Key.Key_Escape and self.text():
            self.clear()
            self.at = len(self.history)
            return
        super().keyPressEvent(event)


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
        # Command buttons: the owner's own, each one command, sent like a typed one.
        self.buttons: list[dict] = []
        self.button_row = QHBoxLayout()
        self.column.addLayout(self.button_row)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(words("buttons.edit_hint"))
        self.editor.setAccessibleName(words("buttons.edit"))
        self.editor.setMaximumHeight(160)
        self.editor.hide()
        self.column.addWidget(self.editor)
        send_row = QHBoxLayout()
        send_row.addWidget(label(">", "address"))
        self.command = CommandLine()
        self.command.setToolTip(words("console.keys_hint"))
        self.names = QStringListModel()
        completer = QCompleter(self.names, self.command)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.command.setCompleter(completer)
        self.usages: dict[str, str] = {}
        self.command.textChanged.connect(self.show_usage)
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
        older = push(words("console.older_logs"), "plain")
        older.clicked.connect(lambda: OlderLogs(self).exec())
        hint.addWidget(older)
        self.column.addLayout(hint)

    def toggle(self, key: str, tip: str, on: bool) -> QPushButton:
        button = push(words(key))
        button.setCheckable(True)
        button.setChecked(on)
        button.setToolTip(words(tip))
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
        self.editor.hide()
        refill(self.button_row, [])
        if typing:
            self.ask("GET", "/command-buttons", self.show_buttons, lambda _e: None)
            self.ask("GET", "/console/commands", self.show_commands, lambda _e: None)
        self.command.history = self.saved_history().get(self.row["id"], [])
        self.command.at = len(self.command.history)
        self.command.setFocus()

    def saved_history(self) -> dict[str, list[str]]:
        try:
            found = json.loads(self.window_.pc.setting("console_history") or "{}")
        except (TypeError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def show_commands(self, data: dict) -> None:
        """The server's own command list (its help), for Tab and the list
        under the box. Empty while it's stopped: nothing is completed."""
        found = data.get("commands") or []
        self.usages = {c["name"]: c["usage"] for c in found}
        self.names.setStringList(list(self.usages))

    def show_usage(self, typed: str) -> None:
        first = typed.strip().split(" ", 1)[0].lower()
        usage = self.usages.get(first)
        self.hint.setText(usage or words("console.hint"))

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
        self.log.setPlainText(streamer.shown("\n".join(lines)))
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
        self.log.appendPlainText(streamer.shown(raw))
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
            if self.command.text().strip() == command:
                self.command.clear()
            self.command.remember(command)
            saved = self.saved_history()
            saved[self.row["id"]] = self.command.history
            self.window_.pc.set_setting("console_history", json.dumps(saved))
            self.say("")

        self.ask(
            "POST",
            "/server/command",
            done,
            lambda exc: self.say(str(exc)),
            body={"command": command, "confirm": True},
            always=lambda: self.send_button.setEnabled(True),
        )

    # ------------------------------------------------------------ command buttons
    def show_buttons(self, data: dict) -> None:
        self.buttons = data.get("buttons") or []
        shown: list[QWidget] = []
        for b in self.buttons:
            button = push(b["name"])
            button.setToolTip(b["command"])
            button.setAccessibleName(f"{b['name']}: {b['command']}")
            button.clicked.connect(lambda _=False, b=b: self.press(b))
            shown.append(button)
        self.edit_button = push(words("buttons.edit"), "plain")
        self.edit_button.clicked.connect(self.edit_buttons)
        refill(self.button_row, [*shown, self.edit_button])
        self.button_row.addStretch()

    def press(self, b: dict) -> None:
        """Checked again by the agent, and a dangerous one asked first."""
        self.checked(b["command"], {"valid": True, "danger_reason": b.get("danger_reason")})

    def edit_buttons(self) -> None:
        if self.editor.isHidden():
            self.editor.setPlainText(
                "\n".join(f"{b['name']}: {b['command']}" for b in self.buttons)
            )
            self.editor.show()
            self.editor.setFocus()
            self.edit_button.setText(words("buttons.save"))
            return
        rows = []
        for line in self.editor.toPlainText().splitlines():
            if not line.strip():
                continue
            name, _, command = line.partition(":")
            rows.append({"name": name.strip()[:24], "command": command.strip()})

        def done(data: dict) -> None:
            self.editor.hide()
            self.show_buttons(data)
            self.say(words("buttons.saved"))

        self.ask(
            "PUT", "/command-buttons", done, lambda exc: self.say(str(exc)), body={"buttons": rows}
        )


class OlderLogs(QDialog):
    """Minecraft's own log files (logs/latest.log and the zipped older
    ones), read-only: pick one on the left, read it on the right."""

    def __init__(self, page: ConsolePage) -> None:
        super().__init__(page.window_)
        self.page = page
        self.setWindowTitle(words("console.older_title"))
        self.resize(900, 600)
        column = QVBoxLayout(self)
        self.said = label(words("console.older_loading"), "sub")
        column.addWidget(self.said)
        row = QHBoxLayout()
        self.files = QListWidget()
        self.files.setAccessibleName(words("console.older_title"))
        self.files.setMaximumWidth(240)
        self.files.currentTextChanged.connect(self.open_file)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        row.addWidget(self.files)
        row.addWidget(self.text, 1)
        column.addLayout(row, 1)
        page.ask("GET", "/logs/files", self.show_files, lambda exc: self.said.setText(str(exc)))

    def show_files(self, data: dict) -> None:
        names = [f["name"] for f in data.get("files") or []]
        self.said.setText("" if names else words("console.older_none"))
        self.files.addItems(names)
        if names:
            self.files.setCurrentRow(0)

    def open_file(self, name: str) -> None:
        if not name:
            return

        def show(data: dict) -> None:
            if self.files.currentItem() and self.files.currentItem().text() == name:
                self.text.setPlainText(streamer.shown("\n".join(data.get("lines") or [])))
                self.said.setText(words("console.older_cut") if data.get("cut") else "")

        self.page.ask(
            "GET", "/logs/files/" + quote(name, safe=""), show, lambda e: self.said.setText(str(e))
        )
