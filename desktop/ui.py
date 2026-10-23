"""Small helpers every screen uses: labels, buttons, times and lists."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QDateTime, QLocale
from PySide6.QtWidgets import (
    QBoxLayout,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .words import text

if TYPE_CHECKING:
    from .window import MainWindow


def label(words: str = "", kind: str = "", name: str = "") -> QLabel:
    widget = QLabel(words)
    widget.setWordWrap(True)
    if kind:
        widget.setProperty("kind", kind)
    if name:
        widget.setObjectName(name)
    return widget


def clock(ts: float | None) -> str:
    """A time as Windows writes it: just the time today, else date and time."""
    if not ts:
        return ""
    when = QDateTime.fromSecsSinceEpoch(int(ts))
    locale = QLocale()
    hours = locale.toString(
        when.time(), locale.timeFormat(QLocale.FormatType.ShortFormat).replace(":ss", "")
    )
    if when.date() == QDateTime.currentDateTime().date():
        return hours
    return f"{locale.toString(when.date(), QLocale.FormatType.ShortFormat)} {hours}"


def duration(seconds: float | None) -> str:
    """How long, the same way in every app: 2d 3h, 4h 5m, 6m or 7s."""
    if seconds is None:
        return text("state.unknown")
    s = max(0, int(seconds))
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    if d:
        return text("time.days_hours", d=d, h=h)
    if h:
        return text("time.hours_minutes", h=h, m=m)
    if m:
        return text("time.minutes", m=m)
    return text("time.seconds", s=s)


def size(n: float | None) -> str:
    """Bytes the way Explorer writes them: 512 KB, 3.4 MB, 12 GB."""
    if n is None:
        return text("state.unknown")
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024
        if n < 1024 or unit == "TB":
            break
    return f"{n:.1f} {unit}" if n < 100 else f"{n:.0f} {unit}"


def has_packs(row: dict) -> bool:
    """A Bedrock server: its add-ons are behavior and resource packs, not mods."""
    return bool((row.get("capabilities") or {}).get("addons"))


def content_page(row: dict) -> str | None:
    """The words for a server's Mods page: Mods, Plugins or Add-ons by its
    type, or None when the type takes none (Vanilla)."""
    if has_packs(row):
        return "page.addons"
    if (row.get("capabilities") or {}).get("mods") or row.get("content"):
        return "page.plugins" if row.get("content") == "plugins" else "page.mods"
    return None


def refill(layout: QBoxLayout, widgets: Sequence[QWidget]) -> None:
    while layout.count():
        old = layout.takeAt(0).widget()
        if old is not None:
            old.setParent(None)
    for widget in widgets:
        layout.addWidget(widget)


def push(words: str, kind: str = "") -> QPushButton:
    widget = QPushButton(words)
    if kind:
        widget.setProperty("kind", kind)
    return widget


def field(words: str, widget: QWidget, hint: str = "") -> QWidget:
    """A form field: its name above, a hint under it when there is one."""
    box = QWidget()
    column = QVBoxLayout(box)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(4)
    column.addWidget(label(words, "sub"))
    column.addWidget(widget)
    widget.setAccessibleName(words)
    if hint:
        column.addWidget(label(hint, "muted"))
    return box


def swatches(palette: list[dict], chosen: str | None, pick: Callable[[str], None]) -> QWidget:
    """The server colors to pick from, as round buttons (one checked)."""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    group = QButtonGroup(box)
    for color in palette:
        swatch = push("", "swatch")
        swatch.setCheckable(True)
        swatch.setChecked(color["hex"] == chosen)
        words = text(f"color.{color['id']}")
        swatch.setToolTip(words)
        swatch.setAccessibleName(words)
        swatch.setStyleSheet(f"background: {color['hex']};")
        swatch.clicked.connect(lambda _=False, c=color["hex"]: pick(c))
        group.addButton(swatch)
        row.addWidget(swatch)
    row.addStretch()
    return box


class Note(QWidget):
    """What a button did, in words, with an Undo button when it can be undone."""

    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.words = label(kind="sub")
        self.undo = push(text("changes.undo"), "plain")
        self.undo.clicked.connect(self.undo_clicked)
        row.addWidget(self.words, 1)
        row.addWidget(self.undo)
        self.then: Callable[[], None] | None = None
        self.hide()

    def say(self, words: str, undo: Callable[[], None] | None = None) -> None:
        self.words.setText(words)
        self.then = undo
        self.undo.setVisible(undo is not None)
        self.setVisible(bool(words))

    def undo_clicked(self) -> None:
        then, self.then = self.then, None
        self.undo.hide()
        if then:
            then()

    def text(self) -> str:
        return self.words.text()


class Page(QScrollArea):
    """One of a server's pages. The server tab calls load() each time it
    opens, changed() after the feed says something changed, and live() with
    each console, chat or metrics event for this server, only while the
    page is open, so a page nobody looks at asks the agent for nothing."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        self.row: dict = {}
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.column = QVBoxLayout(body)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(12)
        self.note = Note()
        self.column.addWidget(self.note)
        self.setWidget(body)

    def say(self, words: str, undo: Callable[[], None] | None = None) -> None:
        self.note.say(words, undo)

    def load(self) -> None:
        pass

    def changed(self) -> None:
        self.load()

    def live(self, event: dict) -> None:
        pass

    def ask(
        self,
        method: str,
        path: str,
        done: Callable[[Any], None],
        failed: Callable[[Exception], None] | None = None,
        body: dict | None = None,
        always: Callable[[], None] | None = None,
        slow: bool = False,
    ) -> None:
        """Call one of this server's routes off the window's thread. An
        answer that comes back after another server was opened is dropped.
        What went wrong shows under the server's name, unless ``failed``.
        ``slow`` for work the agent answers only once done (AgentClient.call)."""
        sid, agent = self.row["id"], self.window_.agent
        here = self.window_.still_on(sid)
        self.window_.run(
            lambda: agent.on(sid, method, path, body, slow),
            here(done),
            here(failed or self.window_.page_problem),
            always,
        )
