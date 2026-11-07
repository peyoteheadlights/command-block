"""A server's Store tab (agent/store.py): connected or not, the store's
link, its packages with prices, Open Tebex, the copy-paste checklist and
the Store ranks. Only the owner, only in Technical, only on a community
server (the server tab decides). The key is typed here once and never shown
again: the agent keeps it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from .gear import buttons
from .overview import card
from .ui import Page, label, push, refill
from .words import text

if TYPE_CHECKING:
    from .window import MainWindow


def store_tab(row: dict) -> bool:
    """Whether a server gets the Store tab at all: a Java server for a
    community that isn't a Test server (the agent says the rest)."""
    return (
        row.get("played_by") == "community"
        and not row.get("test")
        and row.get("edition", "java") == "java"
    )


class StorePage(Page):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        frame, body = card(text("desktop.store.title"))
        body.addWidget(label(text("desktop.store.hint"), "muted"))
        self.status = QVBoxLayout()
        body.addLayout(self.status)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText(text("desktop.store.key"))
        self.key.setAccessibleName(text("desktop.store.key"))
        self.connect_button = push(text("desktop.store.connect"), "primary")
        self.connect_button.clicked.connect(self.connect_store)
        self.check_button = push(text("desktop.store.check"))
        self.check_button.clicked.connect(self.check_store)
        self.open_button = push(text("desktop.store.open_tebex"), "plain")
        self.open_button.clicked.connect(
            lambda: self.window_.pc.open_link(self.data.get("open_tebex", ""))
        )
        self.own_button = push(text("desktop.store.own_package"), "plain")
        self.own_button.clicked.connect(self.own_package)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(self.connect_button)
        body.addLayout(key_row)
        body.addWidget(label(text("desktop.store.key_hint"), "muted"))
        body.addLayout(buttons(self.check_button, self.open_button, self.own_button))
        self.packages = QVBoxLayout()
        body.addLayout(self.packages)
        self.column.addWidget(frame)

        steps, steps_body = card(text("desktop.store.checklist"))
        steps_body.addWidget(label(text("desktop.store.checklist_hint"), "muted"))
        self.checklist = QPlainTextEdit()
        self.checklist.setReadOnly(True)
        self.checklist.setAccessibleName(text("desktop.store.checklist"))
        self.checklist.setMinimumHeight(260)
        steps_body.addWidget(self.checklist)
        copy = push(text("desktop.copy"))
        copy.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(self.checklist.toPlainText())
        )
        steps_body.addLayout(buttons(copy))
        self.column.addWidget(steps)
        self.column.addStretch()
        self.data: dict = {}

    def load(self) -> None:
        self.ask("GET", "/store", self.show_store)

    def show_store(self, data: dict) -> None:
        self.data = data
        lines: list[QWidget] = []
        if not data["offered"]:
            lines.append(label(data["reason"] or "", "muted"))
        elif data["needs_type"]:
            lines.append(label(text("desktop.store.needs_type")))
            change = push(text("perf.open_settings"))
            change.clicked.connect(lambda: self.window_.open_page("Settings"))
            lines.append(change)
        else:
            state = (
                "desktop.store.connected" if data["connected"] else "desktop.store.not_connected"
            )
            lines.append(label(text(state), "step"))
            facts = data.get("store") or {}
            if facts.get("link"):
                lines.append(label(text("desktop.store.link", link=facts["link"]), "sub"))
            lines.append(label(text("desktop.store.buy", command=data["buy_command"]), "muted"))
            if data.get("offline_warning"):
                lines.append(label(data["offline_warning"], "problem"))
            if data.get("ranks_by_hand"):
                lines.append(label(text("desktop.store.ranks_by_hand"), "muted"))
            ranks = ", ".join(r["name"] for r in data["ranks"] if r["made"])
            if ranks:
                lines.append(label(text("desktop.store.ranks", ranks=ranks), "muted"))
            lines.append(label(text("desktop.store.chat_note"), "muted"))
            lines.append(label(data["fees"], "muted"))
        refill(self.status, lines)
        usable = data["offered"] and not data["needs_type"]
        for widget in (self.key, self.connect_button, self.check_button, self.own_button):
            widget.setEnabled(usable)
        self.check_button.setEnabled(usable and data.get("key_saved", False))
        self.show_packages(data.get("packages"))
        self.checklist.setPlainText(data["checklist"] if usable else "")

    def show_packages(self, packages: list[dict] | None) -> None:
        rows: list[QWidget] = []
        if packages is not None:
            rows.append(label(text("desktop.store.packages"), "step"))
            currency = (self.data.get("store") or {}).get("currency") or ""
            for p in packages:
                price = f"{p['price']:g} {currency}" if p.get("price") is not None else ""
                mark = "✓ " if p.get("rank") else ""
                rows.append(label(f"{mark}{p['name']}  {price}".rstrip()))
            if not packages:
                rows.append(label(text("desktop.store.no_packages"), "muted"))
        refill(self.packages, rows)

    def connect_store(self) -> None:
        key = self.key.text().strip()
        if not key:
            self.say(text("desktop.store.key_missing"))
            return
        self.connect_button.setEnabled(False)

        def done(data: dict) -> None:
            self.key.clear()
            self.show_store(data)
            self.say(text("desktop.store.connected_note"))

        self.ask(
            "POST",
            "/store/connect",
            done,
            body={"key": key},
            always=lambda: self.connect_button.setEnabled(True),
            slow=True,
        )

    def check_store(self) -> None:
        def done(data: dict) -> None:
            self.show_store(data)
            if data.get("remind"):
                self.window_.confirm(
                    text("desktop.store.remind_title"),
                    text("desktop.store.remind", names=", ".join(data["remind"]))
                    + "\n\n"
                    + "\n".join(f"- {x}" for x in self.data["never_sold"]),
                    text("desktop.store.got_it"),
                )

        self.ask("POST", "/store/check", done, slow=True)

    def own_package(self) -> None:
        """Adding a package of the owner's own: does it change gameplay?
        Yes shows Mojang's warning. Either way, the never-sold list."""
        never = "\n".join(f"- {x}" for x in self.data.get("never_sold") or [])
        changes = self.window_.confirm(
            text("desktop.store.own_package"),
            text("desktop.store.gameplay_question"),
            text("desktop.store.gameplay_yes"),
            text("desktop.store.gameplay_no"),
        )
        words = (self.data["gameplay_warning"] + "\n\n") if changes else ""
        self.window_.confirm(
            text("desktop.store.own_package"),
            words + text("desktop.store.never_sold") + "\n" + never,
            text("desktop.store.got_it"),
        )
