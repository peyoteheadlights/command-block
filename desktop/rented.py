"""Rented servers on a host's Pterodactyl panel (agent/rented.py): the
Connect a panel dialog, their rows on All servers, and one rented server's
screen with status, Start/Stop/Restart/Kill, the console, the host's
backups, and what needs this PC greyed out with the reason.

The agent holds the key and talks to the panel; this window only calls
/api/rented. The console and status are asked for only while the screen
is open (house rule 7).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .agent import AgentError
from .server_tab import state_words
from .theme import SHARED
from .ui import label, push, refill, size
from .words import text

if TYPE_CHECKING:
    from .window import MainWindow

# What needs this PC (greyed out) and what comes later, as the agent lists
# them, with their words.
NEEDS_THIS_PC = {
    "backups_here": "desktop.rented.local.backups_here",
    "wake": "desktop.rented.local.wake",
    "join_protection": "desktop.rented.local.join_protection",
    "change_type": "desktop.rented.local.change_type",
    "addons": "desktop.rented.local.addons",
}
DISCORD = {
    "off": "desktop.rented.discord.off",
    "status": "desktop.rented.discord.status",
    "control": "desktop.rented.discord.control",
}
STATUS_EVERY_MS = 10_000  # the agent asks the panel every 20 s; this reads its answer
CONSOLE_EVERY_MS = 2_000


def state_line(row: dict) -> str:
    """The state, and why it's Unknown when it is."""
    words = state_words(row)
    if row.get("state") == "UNKNOWN" and row.get("state_reason"):
        return f"{words}: {row['state_reason']}"
    return words


def usage_line(row: dict) -> str:
    """Memory, CPU and disk from the panel; Unknown when it didn't say."""
    r = row.get("resources") or {}
    unknown = text("state.unknown")
    memory = size(r["memory_bytes"]) if r.get("memory_bytes") is not None else unknown
    cpu = f"{r['cpu_percent']:.0f} %" if r.get("cpu_percent") is not None else unknown
    disk = size(r["disk_bytes"]) if r.get("disk_bytes") is not None else unknown
    return text("desktop.rented.usage", memory=memory, cpu=cpu, disk=disk)


class RentedRow(QFrame):
    """One rented server on All servers, marked with the host's name."""

    def __init__(self, window: MainWindow, row: dict) -> None:
        super().__init__()
        self.setProperty("kind", "row")
        line = QHBoxLayout(self)
        line.setContentsMargins(14, 10, 10, 10)
        name = label(row["name"])
        name.setObjectName("title")
        line.addWidget(name, 1)
        line.addWidget(label(row["host"], "chip"))
        line.addWidget(label(state_words(row), "sub"))
        state = row.get("state")
        if window.may(row["id"], "server.control") and state in ("ONLINE", "OFFLINE"):
            action = "stop" if state == "ONLINE" else "start"
            button = push(text(f"action.{action}"), "primary" if action == "start" else "")
            button.setAccessibleName(f"{text(f'action.{action}')} {row['name']}")
            button.clicked.connect(lambda: window.rented_screen.power(row, action))
            line.addWidget(button)
        opens = push(text("desktop.rented.open"), "plain")
        opens.setAccessibleName(f"{text('desktop.rented.open')} {row['name']}")
        opens.clicked.connect(lambda: window.open_rented(row["id"]))
        line.addWidget(opens)


class ConnectPanel(QDialog):
    """Connect a Pterodactyl panel: address and client API key, the owner's
    click to let the agent talk to it, then pick its servers."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.window_ = window
        self.setWindowTitle(text("desktop.rented.connect"))
        self.setMinimumWidth(520)
        column = QVBoxLayout(self)
        column.addWidget(label(text("desktop.rented.connect_intro")))
        self.address = QLineEdit()
        self.address.setPlaceholderText("https://panel.example.com")
        self.address.setAccessibleName(text("desktop.rented.address"))
        column.addWidget(label(text("desktop.rented.address"), "sub"))
        column.addWidget(self.address)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setAccessibleName(text("desktop.rented.key"))
        column.addWidget(label(text("desktop.rented.key"), "sub"))
        column.addWidget(self.key)
        column.addWidget(label(text("desktop.rented.key_hint"), "muted"))
        self.allow = QCheckBox(text("desktop.rented.allow"))
        column.addWidget(self.allow)
        self.problem = label(kind="problem")
        self.problem.hide()
        column.addWidget(self.problem)
        self.warning = label(text("desktop.rented.full_account"), "problem")
        self.warning.hide()
        column.addWidget(self.warning)
        self.picks = QVBoxLayout()
        column.addLayout(self.picks)
        self.boxes: dict[str, QCheckBox] = {}
        row = QHBoxLayout()
        aternos = push(text("desktop.rented.aternos"), "plain")
        aternos.clicked.connect(self.open_aternos)
        row.addWidget(aternos)
        row.addStretch()
        self.look = push(text("desktop.rented.look"), "")
        self.look.clicked.connect(self.check)
        row.addWidget(self.look)
        self.add = push(text("desktop.rented.add"), "primary")
        self.add.setEnabled(False)
        self.add.clicked.connect(self.connect_panel)
        row.addWidget(self.add)
        column.addWidget(label(text("desktop.rented.aternos_note"), "muted"))
        column.addLayout(row)

    def open_aternos(self) -> None:
        links = json.loads((SHARED / "links.json").read_text(encoding="utf-8"))
        self.window_.pc.open_link(links["aternos"])

    def form(self) -> dict[str, Any]:
        return {
            "address": self.address.text().strip(),
            "key": self.key.text().strip(),
            "allow_host": self.allow.isChecked(),
        }

    def say(self, exc: AgentError) -> None:
        self.problem.setText(str(exc))
        self.problem.show()

    def check(self) -> None:
        self.problem.hide()
        body = self.form()
        self.window_.run(
            lambda: self.window_.agent.call("POST", "/rented/check", body), self.found, self.say
        )

    def found(self, answer: dict) -> None:
        self.warning.setVisible(bool(answer.get("full_account")))
        self.boxes = {}
        for server in answer["servers"]:
            box = QCheckBox(server["name"])
            self.boxes[server["identifier"]] = box
        if not self.boxes:
            refill(self.picks, [label(text("desktop.rented.none_on_panel"), "sub")])
        else:
            refill(self.picks, [label(text("desktop.rented.pick"), "sub"), *self.boxes.values()])
        self.add.setEnabled(bool(self.boxes))

    def connect_panel(self) -> None:
        body = {**self.form(), "servers": [i for i, b in self.boxes.items() if b.isChecked()]}

        def done(_answer: dict) -> None:
            self.accept()
            self.window_.refresh()

        self.window_.run(
            lambda: self.window_.agent.call("POST", "/rented/panels", body), done, self.say
        )


class RentedScreen(QWidget):
    NAME = "Rented server"

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        self.row: dict = {}
        self.last_line = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(10)
        self.heading = label()
        self.heading.setObjectName("title")
        outer.addWidget(self.heading)
        self.state = label(kind="sub")
        outer.addWidget(self.state)
        self.usage = label(kind="sub")
        outer.addWidget(self.usage)
        self.problem = label(kind="problem")
        self.problem.hide()
        outer.addWidget(self.problem)
        buttons = QHBoxLayout()
        self.buttons: dict[str, Any] = {}
        for action in ("start", "stop", "restart"):
            button = push(text(f"action.{action}"), "primary" if action == "start" else "")
            button.clicked.connect(lambda _=False, a=action: self.power(self.row, a))
            self.buttons[action] = button
            buttons.addWidget(button)
        kill = push(text("desktop.rented.kill"), "danger")
        kill.clicked.connect(self.kill)
        self.buttons["kill"] = kill
        buttons.addWidget(kill)
        buttons.addStretch()
        outer.addLayout(buttons)
        outer.addWidget(label(text("desktop.rented.console"), name="h2"))
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(500)
        outer.addWidget(self.console, 1)
        line = QHBoxLayout()
        self.command = QLineEdit()
        self.command.setAccessibleName(text("desktop.rented.command"))
        self.command.setPlaceholderText(text("desktop.rented.command"))
        self.command.returnPressed.connect(self.send)
        line.addWidget(self.command, 1)
        self.send_button = push(text("desktop.rented.send"))
        self.send_button.clicked.connect(self.send)
        line.addWidget(self.send_button)
        outer.addLayout(line)
        top = QHBoxLayout()
        top.addWidget(label(text("desktop.rented.backups"), name="h2"), 1)
        self.make_backup = push(text("desktop.rented.make_backup"))
        self.make_backup.clicked.connect(self.backup)
        top.addWidget(self.make_backup)
        outer.addLayout(top)
        self.backups = QVBoxLayout()
        outer.addLayout(self.backups)
        outer.addWidget(label(text("desktop.rented.needs_pc"), name="h2"))
        for words in NEEDS_THIS_PC.values():
            item = label(text(words), "muted")
            item.setEnabled(False)
            outer.addWidget(item)
        outer.addWidget(label(text("desktop.rented.later"), "muted"))
        owner = QHBoxLayout()
        self.discord = QComboBox()
        self.discord.setAccessibleName(text("desktop.rented.discord"))
        for choice, words in DISCORD.items():
            self.discord.addItem(text(words), choice)
        self.discord.activated.connect(self.discord_changed)
        self.discord_label = label(text("desktop.rented.discord"), "sub")
        owner.addWidget(self.discord_label)
        owner.addWidget(self.discord)
        owner.addStretch()
        self.remove = push(text("desktop.rented.remove_key"), "plain")
        self.remove.clicked.connect(self.remove_key)
        owner.addWidget(self.remove)
        outer.addLayout(owner)
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(STATUS_EVERY_MS)
        self.status_timer.timeout.connect(self.load_status)
        self.console_timer = QTimer(self)
        self.console_timer.setInterval(CONSOLE_EVERY_MS)
        self.console_timer.timeout.connect(self.load_console)

    # ---------------------------------------------------------------- showing
    def open(self, row: dict) -> None:
        if row.get("id") != self.row.get("id"):
            self.console.clear()
            self.last_line = 0
            refill(self.backups, [])
        self.show_row(row)
        sid = row["id"]
        may = self.window_.may
        owner = self.window_.role in (None, "owner")
        self.console.setVisible(may(sid, "console.read"))
        self.command.setVisible(may(sid, "console.send"))
        self.send_button.setVisible(may(sid, "console.send"))
        self.make_backup.setVisible(may(sid, "backups.create"))
        for name in ("discord", "discord_label", "remove"):
            getattr(self, name).setVisible(owner)
        self.status_timer.start()
        if may(sid, "console.read"):
            self.console_timer.start()
            self.load_console()
        self.load_backups()

    def hideEvent(self, event) -> None:
        self.status_timer.stop()
        self.console_timer.stop()
        super().hideEvent(event)

    def show_row(self, row: dict) -> None:
        self.row = row
        self.heading.setText(text("desktop.rented.heading", name=row["name"], host=row["host"]))
        self.state.setText(state_line(row))
        self.usage.setText(usage_line(row))
        state = row.get("state")
        control = self.window_.may(row["id"], "server.control")
        known = state != "UNKNOWN"
        self.buttons["start"].setVisible(control)
        self.buttons["start"].setEnabled(known and state == "OFFLINE")
        for action in ("stop", "restart", "kill"):
            self.buttons[action].setVisible(control)
            self.buttons[action].setEnabled(known and state != "OFFLINE")
        index = self.discord.findData(row.get("discord") or "off")
        self.discord.setCurrentIndex(max(index, 0))

    def say(self, exc: AgentError) -> None:
        self.problem.setText(str(exc))
        self.problem.show()

    # ---------------------------------------------------------------- calls
    def call(self, method: str, path: str, body: dict | None = None):
        return lambda: self.window_.agent.call(method, path, body)

    def load_status(self) -> None:
        sid = self.row.get("id")
        if sid:
            self.window_.run(self.call("GET", f"/rented/{sid}/status"), self.show_row)

    def power(self, row: dict, action: str, confirm: bool = False) -> None:
        self.problem.hide()

        def done(_answer: dict) -> None:
            self.window_.refresh()
            self.load_status()

        def failed(exc: AgentError) -> None:
            self.say(exc)
            self.window_.screens["All servers"].failed(exc)  # type: ignore[attr-defined]

        body = {"signal": action, "confirm": confirm}
        self.window_.run(self.call("POST", f"/rented/{row['id']}/power", body), done, failed)

    def kill(self) -> None:
        if self.window_.confirm(
            text("desktop.rented.kill_title", name=self.row["name"]),
            text("desktop.rented.kill_warning"),
            text("desktop.rented.kill"),
        ):
            self.power(self.row, "kill", confirm=True)

    def load_console(self) -> None:
        sid = self.row.get("id")
        if not sid or not self.isVisible():
            return

        def got(answer: dict) -> None:
            for line in answer.get("lines") or []:
                self.console.appendPlainText(line["text"])
            self.last_line = answer.get("last", self.last_line)
            if answer.get("problem"):
                self.problem.setText(answer["problem"])
                self.problem.show()

        self.window_.run(self.call("GET", f"/rented/{sid}/console?after={self.last_line}"), got)

    def send(self) -> None:
        command = self.command.text().strip()
        if not command:
            return

        def done(_answer: dict) -> None:
            self.command.clear()
            self.problem.hide()

        body = {"command": command}
        self.window_.run(
            self.call("POST", f"/rented/{self.row['id']}/command", body), done, self.say
        )

    def load_backups(self) -> None:
        def got(answer: dict) -> None:
            rows: list[QWidget] = []
            for b in answer.get("backups") or []:
                if b.get("succeeded") is None:
                    words = text("desktop.rented.backup_making", name=b.get("name") or "")
                else:
                    words = text(
                        "desktop.rented.backup_row",
                        name=b.get("name") or "",
                        size=size(b.get("bytes")),
                        when=(b.get("completed_at") or "")[:16].replace("T", " "),
                    )
                    if not b["succeeded"]:
                        words += " " + text("desktop.rented.backup_failed")
                rows.append(label(words, "sub"))
            if not rows:
                rows.append(label(text("desktop.rented.no_backups"), "muted"))
            if answer.get("limit") is not None:
                rows.append(label(text("desktop.rented.backup_limit", n=answer["limit"]), "muted"))
            refill(self.backups, rows)

        def failed(exc: AgentError) -> None:
            refill(self.backups, [label(str(exc), "problem")])

        self.window_.run(self.call("GET", f"/rented/{self.row['id']}/backups"), got, failed)

    def backup(self) -> None:
        self.window_.run(
            self.call("POST", f"/rented/{self.row['id']}/backups"),
            lambda _a: self.load_backups(),
            self.say,
        )

    def discord_changed(self, _index: int) -> None:
        body = {"choice": self.discord.currentData()}
        self.window_.run(
            self.call("PUT", f"/rented/{self.row['id']}/discord", body), self.show_row, self.say
        )

    def remove_key(self) -> None:
        if not self.window_.confirm(
            text("desktop.rented.remove_key_title", host=self.row["host"]),
            text("desktop.rented.remove_key_words"),
            text("desktop.rented.remove_key"),
        ):
            return

        def done(_answer: dict) -> None:
            self.window_.show_screen("All servers")
            self.window_.refresh()

        path = f"/rented/panels/{self.row['panel_id']}"
        self.window_.run(self.call("DELETE", path), done, self.say)
