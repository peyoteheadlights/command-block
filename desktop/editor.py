"""Edit settings and add-on guides: two small windows over a page.

The editor opens one config file from the server folder in a text box with
line numbers. Hidden values (tokens, passwords) show as dots until "Show
hidden values"; Save sends the text with the version the file had when it
was opened, and the agent checks it reads as its format first. A refused
save puts the cursor on the line the agent named.

A guide window shows what an add-on does, its steps and its buttons, each
with typed boxes (a number box, a name box that only takes a Minecraft name,
a list). Technical mode also shows the raw command. A button that changes
the world asks first; what the console answered shows under it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from PySide6.QtCore import QRect, QRegularExpression, QSize, Qt
from PySide6.QtGui import QPainter, QRegularExpressionValidator, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .agent import AgentError
from .ui import label, push
from .words import mode, text

if TYPE_CHECKING:
    from .ui import Page

PLAYER_RE = r"^\.?[A-Za-z0-9_]{1,16}$"
NAME_RE = r"^[A-Za-z0-9_\-]{1,32}$"


class _Numbers(QWidget):
    """The line numbers down the editor's left side."""

    def __init__(self, editor: CodeEditor) -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.numbers_width(), 0)

    def paintEvent(self, event) -> None:
        self.editor.paint_numbers(event)


class CodeEditor(QPlainTextEdit):
    """A plain text box with line numbers (the usual Qt pattern: a widget
    in the left margin, painted from the visible text blocks)."""

    def __init__(self) -> None:
        super().__init__()
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.numbers = _Numbers(self)
        self.blockCountChanged.connect(
            lambda _n: self.setViewportMargins(self.numbers_width(), 0, 0, 0)
        )
        self.updateRequest.connect(self._scrolled)
        self.setViewportMargins(self.numbers_width(), 0, 0, 0)

    def numbers_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _scrolled(self, rect: QRect, dy: int) -> None:
        if dy:
            self.numbers.scroll(0, dy)
        else:
            self.numbers.update(0, rect.y(), self.numbers.width(), rect.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        inside = self.contentsRect()
        self.numbers.setGeometry(
            QRect(inside.left(), inside.top(), self.numbers_width(), inside.height())
        )

    def paint_numbers(self, event) -> None:
        painter = QPainter(self.numbers)
        painter.setPen(self.palette().placeholderText().color())
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible():
                painter.drawText(
                    0,
                    top,
                    self.numbers.width() - 6,
                    height,
                    Qt.AlignmentFlag.AlignRight,
                    str(block.blockNumber() + 1),
                )
            top += round(self.blockBoundingRect(block).height())
            block = block.next()
        painter.end()

    def go_to(self, line: int) -> None:
        """Select ``line`` (from 1), so the line the agent named stands out."""
        cursor = QTextCursor(self.document().findBlockByNumber(max(0, line - 1)))
        cursor.select(QTextCursor.SelectionType.LineUnderCursor)
        self.setTextCursor(cursor)
        self.centerCursor()


# ------------------------------------------------------------ Edit settings
def edit_file(page: Page, path: str) -> None:
    """Open ``path`` (inside the server folder) in the editor."""
    page.ask(
        "GET", f"/config-files/file?path={quote(path)}", lambda data: FileEditor(page, data).open()
    )


def pick_file(page: Page, filename: str, name: str) -> None:
    """Edit settings on one add-on: its only file, or a list to pick from."""

    def offer(found: dict) -> None:
        files = [f["path"] for f in found.get("files") or []]
        if not files:
            page.say(text("desktop.files.none_for", name=name))
            return
        if len(files) == 1:
            edit_file(page, files[0])
            return
        index = page.window_.pick(
            text("desktop.files.edit_settings"), text("desktop.files.pick", name=name), files
        )
        if index is not None:
            edit_file(page, files[index])

    page.ask("GET", f"/config-files?addon={quote(filename)}", offer)


class FileEditor(QDialog):
    def __init__(self, page: Page, data: dict) -> None:
        super().__init__(page.window_)
        self.page = page
        self.data = data
        self.setWindowTitle(data["path"])
        self.resize(820, 620)
        column = QVBoxLayout(self)
        column.addWidget(label(data["path"], "step"))
        if not mode["technical"]:
            column.addWidget(label(text("desktop.files.careful"), "notice"))
        self.hidden_row = QWidget()
        line = QHBoxLayout(self.hidden_row)
        line.setContentsMargins(0, 0, 0, 0)
        self.hidden_words = label(kind="sub")
        show = push(text("desktop.files.show_hidden"))
        show.clicked.connect(self.show_hidden)
        line.addWidget(self.hidden_words, 1)
        line.addWidget(show)
        column.addWidget(self.hidden_row)
        self.box = CodeEditor()
        self.box.setAccessibleName(data["path"])
        column.addWidget(self.box, 1)
        self.problem = label(kind="problem")
        self.problem.hide()
        column.addWidget(self.problem)
        buttons = QHBoxLayout()
        self.save = push(text("action.save"), "primary")
        self.save.clicked.connect(self.send)
        close = push(text("desktop.files.close"))
        close.clicked.connect(self.reject)
        buttons.addWidget(self.save)
        buttons.addWidget(close)
        buttons.addStretch()
        column.addLayout(buttons)
        self.fill(data)

    def fill(self, data: dict) -> None:
        self.data = data
        self.box.setPlainText(data["text"])
        hidden = data.get("hidden") or []
        self.hidden_words.setText(text("desktop.files.hidden", count=len(hidden)))
        self.hidden_row.setVisible(bool(hidden))

    def show_hidden(self) -> None:
        if self.box.toPlainText() != self.data["text"] and not self.page.window_.confirm(
            text("desktop.files.show_hidden"),
            text("desktop.files.show_loses"),
            text("desktop.files.show_hidden"),
        ):
            return
        self.page.ask(
            "POST", "/config-files/show", self.fill, self.failed, body={"path": self.data["path"]}
        )

    def send(self) -> None:
        self.problem.hide()
        self.page.ask(
            "POST",
            "/config-files/save",
            self.saved,
            self.failed,
            body={
                "path": self.data["path"],
                "text": self.box.toPlainText(),
                "version": self.data["version"],
            },
        )

    def failed(self, exc: Exception) -> None:
        self.problem.setText(str(exc))
        self.problem.show()
        if isinstance(exc, AgentError) and exc.line:
            self.box.go_to(exc.line)

    def saved(self, result: dict) -> None:
        if result.get("nothing_changed"):
            self.problem.setText(text("settings.nothing_changed"))
            self.problem.show()
            return
        self.accept()
        words = "desktop.files.saved_running" if result.get("running") else "desktop.files.saved"
        self.page.say(
            text(words, file=self.data["path"]),
            (lambda: self.page.window_.move_change("undo")) if result.get("undo") else None,
        )


# ------------------------------------------------------------ guides
def field_box(field: dict) -> tuple[QWidget, Any]:
    """A box that only takes what the field accepts, and how to read it."""
    kind = field["kind"]
    if kind == "number":
        spin = QSpinBox()
        spin.setRange(field["min"], field["max"])
        spin.setValue(field.get("default", field["min"]))
        return spin, spin.value
    if kind == "toggle":
        tick = QCheckBox(field["label"])
        return tick, tick.isChecked
    if kind in ("choice", "world"):
        combo = QComboBox()
        for option in field.get("options") or []:
            combo.addItem(option["label"], option["value"])
        return combo, combo.currentData
    line = QLineEdit()
    line.setMaxLength(16 if kind == "player" else 32)
    pattern = PLAYER_RE if kind == "player" else NAME_RE
    line.setValidator(QRegularExpressionValidator(QRegularExpression(pattern), line))
    return line, lambda: line.text().strip()


class GuideWindow(QDialog):
    def __init__(self, page: Page, guide: dict) -> None:
        super().__init__(page.window_)
        self.page = page
        self.guide = guide
        self.setWindowTitle(guide["title"])
        self.resize(560, 640)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        column = QVBoxLayout(body)
        column.addWidget(label(guide["title"], name="h2"))
        column.addWidget(label(guide["what"]))
        for number, step in enumerate(guide["steps"], 1):
            column.addWidget(label(f"{number}. {step}", "sub"))
        for button in guide["buttons"]:
            column.addWidget(self.button_row(button))
        if mode["technical"]:
            column.addWidget(label(text("desktop.guide.source", link=guide["source"]), "muted"))
        column.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        close = push(text("desktop.files.close"))
        close.clicked.connect(self.accept)
        outer.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

    def button_row(self, button: dict) -> QWidget:
        row = QWidget()
        row.setProperty("kind", "row")
        column = QVBoxLayout(row)
        column.setContentsMargins(12, 8, 8, 8)
        if button.get("help"):
            column.addWidget(label(button["help"], "sub"))
        reads: dict[str, Any] = {}
        line = QHBoxLayout()
        for field in button.get("fields") or []:
            widget, read = field_box(field)
            widget.setAccessibleName(field["label"])
            if field["kind"] != "toggle":
                line.addWidget(label(field["label"], "sub"))
            line.addWidget(widget)
            reads[field["id"]] = read
        press = push(button["label"], "primary" if not reads else "")
        press.setAccessibleName(f"{self.guide['title']}: {button['label']}")
        line.addWidget(press)
        line.addStretch()
        column.addLayout(line)
        if mode["technical"]:
            column.addWidget(label(button["command"], "muted"))
        answer = label(kind="sub")
        answer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        answer.hide()
        column.addWidget(answer)
        press.clicked.connect(lambda: self.press(button, reads, answer))
        return row

    def press(self, button: dict, reads: dict[str, Any], answer) -> None:
        values = {key: read() for key, read in reads.items()}
        if button.get("changes_world") and not self.page.window_.confirm(
            button["label"],
            button.get("warning") or text("desktop.guide.changes_world"),
            button["label"],
        ):
            return

        def done(result: dict) -> None:
            lines = [line["raw"] for line in result.get("lines") or []]
            answer.setText("\n".join(lines[-12:]) if lines else text("desktop.guide.no_answer"))
            answer.show()

        def failed(exc: Exception) -> None:
            answer.setText(str(exc))
            answer.show()

        answer.setText(text("desktop.guide.sending"))
        answer.show()
        self.page.ask(
            "POST",
            f"/guides/{self.guide['id']}/{button['id']}",
            done,
            failed,
            body={"values": values, "confirm": bool(button.get("changes_world"))},
            slow=True,
        )
