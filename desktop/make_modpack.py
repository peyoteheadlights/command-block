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
    from .mods import ModsPage, VanillaModsPage


class MakeDialog(QDialog):
    """Name, version, "Leave out server-only mods", the players' loader on
    a plugin server, the config files to put in, "Required to join", the
    chat line and the title screen (all need the Companion mod on the
    server). ``pick_background`` uploads a picture and returns the new
    options, or None. ``change_type`` opens Change server type (vanilla)."""

    def __init__(
        self, parent: QWidget, options: dict, pick_background=None, change_type=None
    ) -> None:
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
        hint = "make.required_hint" if join.get("available") else "make.required_later"
        if join.get("needs_fabric"):
            hint = "make.required_vanilla"
        column.addWidget(label(text(hint), "sub"))
        if join.get("needs_fabric") and change_type:
            change = push(text("make.change_type"))
            change.clicked.connect(self.reject)  # close this first, then open Settings
            change.clicked.connect(change_type)
            column.addWidget(change)
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
        self.title_screen(column, options, pick_background, bool(join.get("available")))

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

    def title_screen(self, column, options: dict, pick_background, shown: bool) -> None:
        """The join button beside Realms, the yellow flavor text and the
        background picture, for friends with the Companion."""
        title = options.get("title_screen") or {}
        button, flavor = title.get("button") or {}, title.get("flavor_text") or {}
        self.button_on = QCheckBox(text("make.title_button"))
        self.button_on.setChecked(bool(button.get("on")))
        self.address = QLineEdit(button.get("address") or "")
        self.address.setMaxLength(260)
        self.address.setPlaceholderText(text("make.title_address"))
        self.flavor_on = QCheckBox(text("make.flavor_text"))
        self.flavor_on.setChecked(bool(flavor.get("on")))
        self.flavor = QLineEdit(flavor.get("text") or "")
        self.flavor.setMaxLength(60)
        self.background_on = QCheckBox(text("make.background"))
        self.background_on.setChecked(bool((title.get("background") or {}).get("on")))
        self.background_on.setEnabled(bool(title.get("background_set")))
        pick = push(text("make.background_pick"))

        def picked() -> None:
            got = pick_background() if pick_background else None
            if got and (got.get("title_screen") or {}).get("background_set"):
                self.background_on.setEnabled(True)
                self.background_on.setChecked(True)

        pick.clicked.connect(picked)
        line = QHBoxLayout()
        line.addWidget(self.background_on)
        line.addWidget(pick)
        line.addStretch()
        widgets = [
            label(text("make.title_screen")),
            label(text("make.title_screen_hint"), "sub"),
            self.button_on,
            self.address,
            self.flavor_on,
            self.flavor,
        ]
        for widget in widgets:
            widget.setVisible(shown)
            column.addWidget(widget)
        holder = QWidget()
        holder.setLayout(line)
        holder.setVisible(shown)
        column.addWidget(holder)
        self.button_on.toggled.connect(self.address.setEnabled)
        self.flavor_on.toggled.connect(self.flavor.setEnabled)
        self.address.setEnabled(self.button_on.isChecked())
        self.flavor.setEnabled(self.flavor_on.isChecked())

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
            "title_screen": {
                "button": {
                    "on": self.button_on.isChecked(),
                    "address": self.address.text().strip(),
                },
                "flavor_text": {
                    "on": self.flavor_on.isChecked(),
                    "text": self.flavor.text().strip(),
                },
                "background": {"on": self.background_on.isChecked()},
            },
        }


class MakeCard:
    """The Make a modpack card. Shown to accounts that may manage mods."""

    def __init__(self, page: ModsPage | VanillaModsPage) -> None:
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
        dialog = MakeDialog(
            self.page,
            options,
            self.pick_background,
            lambda: self.page.window_.open_page("Settings"),
        )
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

    def pick_background(self) -> dict | None:
        """Upload the title-screen picture now; waits, as the dialog is open."""
        path = self.page.window_.pc.pick_file(text("make.background"), "PNG (*.png)")
        if not path:
            return None
        sid, agent = self.page.row["id"], self.page.window_.agent
        try:
            return agent.upload(f"/servers/{sid}/modpack/title-background", path)
        except Exception as exc:  # the agent's own words, like "Choose a PNG picture."
            self.page.said(exc)
            return None

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
