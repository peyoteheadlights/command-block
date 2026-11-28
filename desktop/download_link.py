"""The Download link card on the Mods page (agent/downloadlink.py): turn
the link on or off, copy the modpack's link, and the server resource pack
(a zip the link serves, or an outside link, and "Players must accept the
resource pack").

Qt note: QInputDialog.getText is a small "type one line" window, like
JOptionPane.showInputDialog in Java Swing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QInputDialog

from .overview import card
from .ui import label, push, size
from .words import text

if TYPE_CHECKING:
    from .mods import ModsPage, VanillaModsPage


class LinkCard:
    """Shown on Java servers to accounts that may manage mods. Turning the
    link on is the owner's (PC settings); the pack needs Game settings."""

    def __init__(self, page: ModsPage | VanillaModsPage) -> None:
        self.page = page
        self.data: dict = {}
        self.frame, layout = card(text("link.title"))
        layout.addWidget(label(text("link.intro"), "sub"))
        self.words = label()
        layout.addWidget(self.words)
        self.switch = push(text("link.turn_on"), "primary")
        self.switch.clicked.connect(self.flip)
        self.new = push(text("link.new"))
        self.new.clicked.connect(lambda: self.send("POST", "/download-link/new"))
        self.copy = push(text("link.copy"))
        self.copy.clicked.connect(self.copy_link)
        line = QHBoxLayout()
        for button in (self.switch, self.new, self.copy):
            line.addWidget(button)
        line.addStretch()
        layout.addLayout(line)

        layout.addWidget(label(text("pack.title"), name="h2"))
        layout.addWidget(label(text("pack.intro"), "sub"))
        self.pack_words = label()
        layout.addWidget(self.pack_words)
        self.required = QCheckBox(text("pack.required"))
        self.required.clicked.connect(
            lambda on: self.send("POST", "/resource-pack", {"required": on})
        )
        layout.addWidget(self.required)
        self.pick = push(text("pack.pick"))
        self.pick.clicked.connect(self.pick_pack)
        self.use_link = push(text("pack.use_link"))
        self.use_link.clicked.connect(self.ask_link)
        self.remove = push(text("pack.remove"))
        self.remove.clicked.connect(lambda: self.send("DELETE", "/resource-pack"))
        line = QHBoxLayout()
        for button in (self.pick, self.use_link, self.remove):
            line.addWidget(button)
        line.addStretch()
        layout.addLayout(line)
        layout.addWidget(label(text("pack.tailscale_only"), "sub"))
        self.frame.setVisible(False)

    def show(self, row: dict) -> None:
        java = row.get("edition") != "bedrock" and self.page.may("mods.manage")
        self.frame.setVisible(java)
        if java:
            self.load()

    def load(self) -> None:
        self.page.ask("GET", "/download-link", self.fill, lambda _e: None)

    def fill(self, data: dict) -> None:
        self.data = data
        on, owner = data.get("on"), self.page.may("pc.settings")
        lines = []
        if on and not data.get("listening") and data.get("why_not"):
            lines.append(text("link.not_listening", why=data["why_not"]))
        elif on:
            url = data.get("modpack_url")
            lines.append(text("link.modpack", url=url) if url else text("link.no_modpack"))
            lines.append(text("link.speed", speed=size(data.get("speed_cap_bytes"))))
            lines.append(text("link.new_hint"))
        elif data.get("why_not"):
            lines.append(data["why_not"])
        if not owner:
            lines.append(text("link.owner_only"))
        self.words.setText("\n".join(lines))
        self.switch.setText(text("link.turn_off" if on else "link.turn_on"))
        self.switch.setEnabled(owner and (on or not data.get("why_not")))
        self.new.setVisible(bool(on) and owner)
        self.copy.setVisible(bool(data.get("modpack_url")))

        pack = data.get("resource_pack")
        game = self.page.may("settings.game")
        if not pack:
            pack_lines = [text("pack.none")]
        elif pack.get("served"):
            key = "pack.file" if pack.get("url") else "pack.file_off"
            pack_lines = [text(key, name=pack.get("filename"), size=size(pack.get("size_bytes")))]
        else:
            pack_lines = [text("pack.outside", url=pack.get("url"))]
        if pack and not pack.get("sha1"):
            pack_lines.append(text("pack.no_sha1"))
        if data.get("restart_needed"):
            pack_lines.append(text("pack.restart"))
        self.pack_words.setText("\n".join(pack_lines))
        self.required.setVisible(bool(pack))
        self.required.setChecked(bool(pack and pack.get("required")))
        self.required.setEnabled(game)
        for button in (self.pick, self.use_link):
            button.setEnabled(game)
        self.remove.setVisible(bool(pack))
        self.remove.setEnabled(game)

    def send(self, method: str, route: str, body: dict | None = None) -> None:
        self.page.ask(method, route, self.fill, self.failed, body=body, slow=True)

    def failed(self, exc: Exception) -> None:
        self.page.said(exc)
        self.load()

    def flip(self) -> None:
        self.send("POST", "/download-link", {"on": not self.data.get("on")})

    def copy_link(self) -> None:
        QGuiApplication.clipboard().setText(self.data.get("modpack_url") or "")
        self.page.say(text("link.copied"))

    def pick_pack(self) -> None:
        path = self.page.window_.pc.pick_file(text("pack.title"), f"{text('pack.title')} (*.zip)")
        if not path:
            return
        sid, agent = self.page.row["id"], self.page.window_.agent
        required = str(self.required.isChecked()).lower()
        self.page.run_here(
            lambda: agent.upload(f"/servers/{sid}/resource-pack/upload?required={required}", path),
            self.fill,
            self.failed,
        )

    def ask_link(self) -> None:
        url, ok = QInputDialog.getText(self.page, text("pack.title"), text("pack.link_prompt"))
        if not ok or not url.strip():
            return
        sha1, ok = QInputDialog.getText(self.page, text("pack.title"), text("pack.sha1_prompt"))
        if not ok:
            return
        body = {"url": url.strip(), "sha1": sha1.strip(), "required": self.required.isChecked()}
        self.send("POST", "/resource-pack", body)
