"""Make a modpack from a server and share it (agent/modpackmake.py): the
card on the Mods page, the question asked before it's made, and what can
be done with the file (save it, have the Discord bot post it, share it
from the Modrinth App).

Qt note: a QDialog is a window that waits (``exec``) until OK or Cancel,
like a JOptionPane in Java Swing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .overview import card
from .ui import label, push, size
from .words import text

if TYPE_CHECKING:
    from .mods import ModsPage


class MakeDialog(QDialog):
    """Name, version, "Leave out server-only mods", the players' loader on
    a plugin server, the config files to put in, "Required to join" and
    the chat line (both need the Companion mod on the server)."""

    def __init__(self, parent: QWidget, options: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle(text("make.title"))
        column = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(options.get("name") or "")
        self.name.setMaxLength(64)
        self.version = QLineEdit(options.get("version") or "1")
        self.version.setMaxLength(32)
        form.addRow(text("make.name"), self.name)
        form.addRow(text("make.version"), self.version)
        self.loader = QComboBox()
        for loader in options.get("players_loaders") or []:
            self.loader.addItem(loader["name"], loader["id"])
        if self.loader.count():
            form.addRow(text("make.loader"), self.loader)
        column.addLayout(form)
        self.leave_out = QCheckBox(text("make.leave_out"))
        self.leave_out.setChecked(True)
        column.addWidget(self.leave_out)
        column.addWidget(label(text("make.leave_out_hint"), "sub"))
        join = options.get("required_to_join") or {}
        chat = options.get("chat_line") or {}
        self.required = QCheckBox(text("make.required"))
        self.required.setEnabled(bool(join.get("available")))
        self.required.setChecked(bool(join.get("available") and join.get("on")))
        column.addWidget(self.required)
        column.addWidget(
            label(
                text("make.required_hint" if join.get("available") else "make.required_later"),
                "sub",
            )
        )
        self.chat_on = QCheckBox(text("make.chat_line"))
        self.chat_on.setChecked(bool(chat.get("on", True)))
        self.chat_text = QLineEdit(chat.get("text") or "")
        self.chat_text.setMaxLength(300)
        for widget in (self.chat_on, self.chat_text, label(text("make.chat_line_hint"), "sub")):
            widget.setVisible(bool(join.get("available")))
            column.addWidget(widget)
        self.required.toggled.connect(self.chat_on.setDisabled)
        self.required.toggled.connect(self.chat_text.setDisabled)
        self.chat_on.setDisabled(self.required.isChecked())
        self.chat_text.setDisabled(self.required.isChecked())

        configs = options.get("configs") or []
        column.addWidget(label(text("make.configs" if configs else "make.no_configs")))
        rows = QWidget()
        ticks = QVBoxLayout(rows)
        self.configs: list[tuple[QCheckBox, str]] = []
        for config in configs:
            tick = QCheckBox(f"{config['path']} ({size(config['size_bytes'])})")
            ticks.addWidget(tick)
            self.configs.append((tick, config["path"]))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(rows)
        scroll.setMaximumHeight(240)
        scroll.setVisible(bool(configs))
        column.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(text("make.title"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)

    def body(self) -> dict[str, Any]:
        return {
            "name": self.name.text().strip(),
            "version": self.version.text().strip(),
            "leave_out_server_only": self.leave_out.isChecked(),
            "configs": [path for tick, path in self.configs if tick.isChecked()],
            "players_loader": self.loader.currentData() or "fabric",
            "required": self.required.isChecked(),
            # Left empty: the agent's own wording.
            "chat_line": {"on": self.chat_on.isChecked(), "text": self.chat_text.text().strip()}
            if self.chat_text.text().strip()
            else None,
        }


class MakeCard:
    """The Make a modpack card. Shown to accounts that may manage mods."""

    def __init__(self, page: ModsPage) -> None:
        self.page = page
        self.result: dict | None = None
        self.frame, layout = card(text("make.title"))
        layout.addWidget(label(text("make.intro"), "sub"))
        self.start = push(text("make.start"))
        self.start.clicked.connect(self.ask_first)
        self.words = label()
        self.save = push(text("make.save"), "primary")
        self.save.clicked.connect(self.save_file)
        self.discord = push(text("make.discord"))
        self.discord.clicked.connect(self.post)
        line = QHBoxLayout()
        for button in (self.start, self.save, self.discord):
            line.addWidget(button)
        line.addStretch()
        layout.addWidget(self.words)
        layout.addLayout(line)
        self.guide = label(text("make.guide_title") + "\n" + text("make.guide"), "sub")
        layout.addWidget(self.guide)
        self.show_result()

    def show(self, row: dict) -> None:
        self.frame.setVisible(row.get("edition") != "bedrock" and self.page.may("mods.manage"))

    def show_result(self) -> None:
        made = self.result
        for widget in (self.words, self.save, self.guide):
            widget.setVisible(bool(made))
        self.discord.setVisible(bool(made and made.get("discord")))
        if not made:
            return
        lines = [text("make.made", name=made["filename"], size=size(made["size_bytes"]))]
        if made.get("trust_note"):
            lines.append(text("make.trust"))
        for key, names in (
            ("make.get_yourself", [n["name"] for n in made.get("get_yourself") or []]),
            ("make.plugins_to_get", made.get("plugins_to_get")),
            ("make.left_out", made.get("left_out")),
        ):
            if names:
                lines.append(text(key, names=", ".join(names)))
        self.words.setText("\n\n".join(lines))

    def ask_first(self) -> None:
        self.page.ask("GET", "/modpack/make", self.ask_details, self.page.said)

    def ask_details(self, options: dict) -> None:
        dialog = MakeDialog(self.page, options)
        if not dialog.exec():
            return
        self.start.setEnabled(False)
        self.page.say(text("make.making"))

        def made(result: dict) -> None:
            self.result = result
            self.page.say("")
            self.show_result()
            self.page.link_card.load()  # the modpack's download link, if it's on
            self.page.modrinth_card.load()  # "Upload Friends 2"

        self.page.ask(
            "POST",
            "/modpack/make",
            made,
            self.page.said,
            body=dialog.body(),
            always=lambda: self.start.setEnabled(True),
            slow=True,
        )

    def save_file(self) -> None:
        made = self.result or {}
        path = self.page.window_.pc.save_path(made["filename"])
        if not path:
            return
        sid, agent = self.page.row["id"], self.page.window_.agent
        route = f"/servers/{sid}/modpack/made/{quote(made['token'])}"
        self.page.run_here(
            lambda: agent.download_to(route, path),
            lambda _r: self.page.say(text("desktop.saved", path=path)),
        )

    def post(self) -> None:
        made = self.result or {}
        self.discord.setEnabled(False)
        self.page.ask(
            "POST",
            "/modpack/made/discord",
            lambda r: self.page.say(
                text("make.posted_link" if r.get("as_link") else "make.posted", count=r["posted"])
            ),
            self.page.said,
            body={"token": made["token"]},
            always=lambda: self.discord.setEnabled(True),
        )
