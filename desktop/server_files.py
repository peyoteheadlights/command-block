"""Settings > Server > Server files: look through the server's folder,
download a file, upload one into the folder that's open. Owner only.

The agent checks every path (only inside the server folder, never through a
link) and never replaces a file that's already there. Editing stays with
Edit settings, further down the Game section.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from .overview import card
from .ui import clock, label, push, size
from .words import text

if TYPE_CHECKING:
    from .settings import SettingsPage


class FilesCard(QWidget):
    def __init__(self, page: SettingsPage) -> None:
        super().__init__()
        self.page = page
        self.path = ""
        self.up_to: str | None = None
        frame, layout = card(text("files.title"), words=text("files.hint"))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        top = QHBoxLayout()
        self.up = push(text("files.up"), "plain")
        self.up.clicked.connect(lambda: self.load(self.up_to or ""))
        self.where = label(kind="address")
        top.addWidget(self.up)
        top.addWidget(self.where, 1)
        layout.addLayout(top)
        self.listed = QListWidget()
        self.listed.setAccessibleName(text("files.title"))
        self.listed.setMinimumHeight(220)
        self.listed.itemDoubleClicked.connect(self.open_item)
        self.listed.currentItemChanged.connect(lambda *_: self.show_buttons())
        layout.addWidget(self.listed)
        self.said = label(kind="sub")
        layout.addWidget(self.said)
        buttons = QHBoxLayout()
        self.download = push(text("files.download"), "tonal")
        self.download.clicked.connect(self.download_chosen)
        self.upload = push(text("files.upload"))
        self.upload.clicked.connect(self.upload_here)
        buttons.addWidget(self.download)
        buttons.addWidget(self.upload)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.show_buttons()

    def load(self, path: str = "") -> None:
        self.said.setText("")
        self.page.ask(
            "GET", f"/files?path={quote(path)}", self.fill, lambda exc: self.said.setText(str(exc))
        )

    def fill(self, data: dict) -> None:
        self.path, self.up_to = data["path"], data.get("parent")
        self.where.setText("/" + self.path)
        self.up.setEnabled(self.up_to is not None)
        self.listed.clear()
        for entry in data.get("entries") or []:
            if entry["folder"]:
                words = f"{entry['name']}/"
            else:
                words = f"{entry['name']}  ·  {size(entry['size'])}  ·  {clock(entry['changed'])}"
            item = QListWidgetItem(words)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.listed.addItem(item)
        if data.get("truncated"):
            self.said.setText(text("files.truncated"))
        elif not data.get("entries"):
            self.said.setText(text("files.empty"))
        self.show_buttons()

    def chosen(self) -> dict | None:
        item = self.listed.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def show_buttons(self) -> None:
        entry = self.chosen()
        self.download.setEnabled(bool(entry) and not entry["folder"])  # type: ignore[index]

    def inside(self, name: str) -> str:
        return f"{self.path}/{name}" if self.path else name

    def open_item(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry["folder"]:
            self.load(self.inside(entry["name"]))
        else:
            self.download_chosen()

    def download_chosen(self) -> None:
        entry = self.chosen()
        if not entry or entry["folder"]:
            return
        target = self.page.window_.pc.save_path(entry["name"])
        if not target:
            return
        sid, agent = self.page.row["id"], self.page.window_.agent
        route = f"/servers/{sid}/files/download?path={quote(self.inside(entry['name']))}"
        self.said.setText(text("files.downloading"))
        self.page.window_.run(
            lambda: agent.download_to(route, target),
            lambda _r: self.said.setText(text("files.downloaded", place=target)),
            lambda exc: self.said.setText(str(exc)),
        )

    def upload_here(self) -> None:
        file = self.page.window_.pc.pick_file(text("files.upload"), text("files.any_file"))
        if not file:
            return
        sid, agent = self.page.row["id"], self.page.window_.agent
        route = f"/servers/{sid}/files/upload?path={quote(self.path)}"
        self.said.setText(text("files.uploading"))

        def done(saved: dict) -> None:
            self.load(self.path)
            self.page.say(text("files.uploaded", name=saved["name"]))

        self.page.window_.run(
            lambda: agent.upload(route, file), done, lambda exc: self.said.setText(str(exc))
        )
