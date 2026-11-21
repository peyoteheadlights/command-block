"""How the server shows in Minecraft's server list: the MOTD builder (the
words under the name, with colours) and the server icon picker.

Minecraft writes colours into the text itself: a § and one letter or digit,
like §6 for gold or §l for bold. The buttons put those codes in where the
cursor is, and the preview shows what players will see.
"""

from __future__ import annotations

import base64
import html
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from .overview import card
from .ui import field, label, push
from .words import text

if TYPE_CHECKING:
    from .settings import SettingsPage

# Minecraft's 16 text colours, by code (the same on Java and Bedrock).
COLORS = {
    "0": "#000000",
    "1": "#0000AA",
    "2": "#00AA00",
    "3": "#00AAAA",
    "4": "#AA0000",
    "5": "#AA00AA",
    "6": "#FFAA00",
    "7": "#AAAAAA",
    "8": "#555555",
    "9": "#5555FF",
    "a": "#55FF55",
    "b": "#55FFFF",
    "c": "#FF5555",
    "d": "#FF55FF",
    "e": "#FFFF55",
    "f": "#FFFFFF",
}
# Bold, strikethrough, underline, italic, and reset (back to plain grey).
STYLES = {
    "l": "font-weight:bold",
    "m": "text-decoration:line-through",
    "n": "text-decoration:underline",
    "o": "font-style:italic",
}
PLAIN = "#AAAAAA"  # what the server list shows text in before any colour
ICON = 64


def preview_html(motd: str) -> str:
    """The MOTD as Minecraft's server list draws it, as rich text. A colour
    code ends bold, italic and the rest, the way Java Edition does."""
    out: list[str] = []
    color: str = PLAIN
    styles: list[str] = []
    i = 0
    while i < len(motd):
        if motd[i] == "§" and i + 1 < len(motd):
            code = motd[i + 1].lower()
            if code in COLORS:
                color, styles = COLORS[code], []
            elif code in STYLES and STYLES[code] not in styles:
                styles.append(STYLES[code])
            elif code == "r":
                color, styles = PLAIN, []
            i += 2  # unknown codes (and §k, scrambled) show nothing, as in the game
            continue
        j = i
        while j < len(motd) and motd[j] != "§":
            j += 1
        piece = html.escape(motd[i:j]).replace("\n", "<br>")
        out.append(f'<span style="color:{color};{";".join(styles)}">{piece}</span>')
        i = j
    return "".join(out)


def icon_png(path: str) -> bytes | None:
    """Any picture Qt can read, cut to a square from its middle and made
    64 x 64, as PNG. None when the file isn't a picture."""
    image = QImage(path)
    if image.isNull():
        return None
    side = min(image.width(), image.height())
    square = image.copy(
        QRect((image.width() - side) // 2, (image.height() - side) // 2, side, side)
    )
    small = square.scaled(
        ICON, ICON, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    # The stubs want bytes for the format; PySide itself wants str.
    small.save(buffer, "PNG")  # type: ignore[call-overload]
    return bytes(data.data())


def list_card(page: SettingsPage, f: dict, data: dict) -> QFrame:
    """The MOTD (or a Bedrock server's name) with colour buttons and a
    preview, and on Java the server icon."""
    frame, layout = card(text("motd.title"), words=text("motd.codes_hint"))
    frame.setEnabled(page.may("settings.game"))
    first = str(f["value"] if f.get("set") and not f.get("problem") else f["minecraft_default"])
    lines = [QLineEdit(part) for part in (first.split("\n") + [""])[: f.get("lines") or 1]]
    focused = [lines[0]]

    # The preview: the icon, then the words, on the server list's dark background.
    row = QHBoxLayout()
    picture = QLabel()
    picture.setFixedSize(ICON, ICON)
    picture.setAccessibleName(text("motd.icon"))
    shown = QLabel()
    shown.setTextFormat(Qt.TextFormat.RichText)
    shown.setWordWrap(True)
    shown.setAccessibleName(text("motd.preview"))
    preview = QFrame()
    preview.setStyleSheet("background:#1d1d1d; border-radius:6px;")
    inside = QHBoxLayout(preview)
    if data.get("icon") is not None:
        inside.addWidget(picture)
    inside.addWidget(shown, 1)
    row.addWidget(preview, 1)
    layout.addLayout(row)

    def show() -> None:
        shown.setText(preview_html("\n".join(line.text() for line in lines)))

    for number, line in enumerate(lines, 1):
        line.setMaxLength(f.get("max_length") or 150)
        line.textChanged.connect(lambda _t: show())
        line.cursorPositionChanged.connect(lambda _a, _b, w=line: focused.__setitem__(0, w))
        words = (
            text("motd.line", number=number)
            if len(lines) > 1
            else text(f"game.{'server_name' if f['key'] == 'server-name' else 'motd'}")
        )
        layout.addWidget(field(words, line))
    show()

    def put(code: str) -> None:
        focused[0].insert(f"§{code}")
        focused[0].setFocus()

    swatches = QHBoxLayout()
    swatches.setSpacing(4)
    for code, color in COLORS.items():
        button = push("", "swatch")
        words = text(f"motd.color_{code}")
        button.setToolTip(words)
        button.setAccessibleName(words)
        button.setStyleSheet(f"background: {color};")
        button.clicked.connect(lambda _=False, c=code: put(c))
        swatches.addWidget(button)
    swatches.addStretch()
    layout.addWidget(label(text("motd.colors"), "sub"))
    layout.addLayout(swatches)
    styles = QHBoxLayout()
    for code, words in (
        ("l", "motd.bold"),
        ("o", "motd.italic"),
        ("n", "motd.underline"),
        ("m", "motd.strike"),
        ("r", "motd.plain"),
    ):
        button = push(text(words), "plain")
        button.clicked.connect(lambda _=False, c=code: put(c))
        styles.addWidget(button)
    styles.addStretch()
    layout.addLayout(styles)

    save_row = QHBoxLayout()
    save = push(text("motd.save"), "tonal")
    said = label(kind="sub")
    save_row.addWidget(save)
    save_row.addWidget(said, 1)
    layout.addLayout(save_row)

    def saved(result: dict) -> None:
        page.window_.load_server_parts()
        page.done(text("game.saved_running" if result.get("running") else "game.saved"))

    def save_text() -> None:
        value = "\n".join(line.text() for line in lines).rstrip("\n")
        if value == first:
            said.setText(text("settings.nothing_changed"))
            return
        page.ask(
            "PUT",
            "/game-settings",
            saved,
            lambda exc: said.setText(str(getattr(exc, "problems", {}).get(f["key"]) or exc)),
            body={"values": {f["key"]: value}},
        )

    save.clicked.connect(save_text)
    if data.get("icon") is not None:
        layout.addWidget(icon_row(page, picture, bool(data["icon"])))
    return frame


def icon_row(page: SettingsPage, picture: QLabel, has_icon: bool) -> QFrame:
    """Pick a picture / Remove, and the icon shown in the preview."""
    holder = QFrame()
    column = QVBoxLayout(holder)
    column.setContentsMargins(0, 0, 0, 0)
    column.addWidget(label(text("motd.icon"), name="h3"))
    column.addWidget(label(text("motd.icon_hint"), "sub"))
    row = QHBoxLayout()
    pick = push(text("motd.icon_pick"))
    remove = push(text("motd.icon_remove"), "plain")
    remove.setVisible(has_icon)
    said = label(kind="problem")
    said.hide()
    row.addWidget(pick)
    row.addWidget(remove)
    row.addStretch()
    column.addLayout(row)
    column.addWidget(said)

    def shown(result: dict) -> None:
        pixmap = QPixmap()
        if result.get("png"):
            pixmap.loadFromData(base64.b64decode(result["png"]), "PNG")  # type: ignore[call-overload]
        picture.setPixmap(pixmap)

    if has_icon:
        page.ask("GET", "/server-icon", shown, lambda _e: None)

    def failed(exc: Exception) -> None:
        said.setText(str(exc))
        said.show()

    def changed(words: str) -> Callable[[dict], None]:
        def after(result: dict) -> None:
            page.window_.load_server_parts()
            undo = (lambda: page.window_.move_change("undo")) if result.get("undo") else None
            page.say(
                text(words) + (" " + text("motd.icon_restart") if result.get("running") else ""),
                undo,
            )
            page.load()

        return after

    def choose() -> None:
        path, _ = QFileDialog.getOpenFileName(
            page,
            text("motd.icon_pick"),
            "",
            text("motd.icon_files") + " (*.png *.jpg *.jpeg *.bmp *.gif *.webp)",
        )
        if not path:
            return
        png = icon_png(path)
        if png is None:
            failed(Exception(text("motd.icon_unreadable")))
            return
        said.hide()
        body = {"png": base64.b64encode(png).decode()}
        page.ask("PUT", "/server-icon", changed("motd.icon_saved"), failed, body=body)

    pick.clicked.connect(choose)
    remove.clicked.connect(
        lambda: page.ask("DELETE", "/server-icon", changed("motd.icon_removed"), failed)
    )
    return holder
