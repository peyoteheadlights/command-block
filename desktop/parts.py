"""The parts the pages are built from: the raised box, the icon button,
the info dot, the switch, the segmented control, the slider, the big number
you can type in, the search field, the status mark and the player face.

The ones a Qt style sheet can draw are in ``theme.py`` (the buttons, the
tick box). The rest paint themselves with QPainter, because a style sheet
can't move a handle or draw a soft shadow. A part finds its colors by
itself (``look_of``), so a page only has to put it on the screen.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QLocale,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from . import icons, theme
from .words import text

EDGE = 1.5  # the line around a part, the same as around the paper
RING = 2  # room around a part for the line that shows it has the keyboard


def look_of(widget: QWidget | None) -> dict[str, str]:
    """The colors of the window ``widget`` is in. The main window keeps them
    in ``look``; a part that is on no window yet gets the light theme."""
    while widget is not None:
        look = getattr(widget, "look", None)
        if look:
            return look
        widget = widget.parentWidget()
    return theme.tokens(False)


def animated(widget: QWidget | None) -> bool:
    """Windows' "Show animations", asked through the window's ``pc``. Off
    over Remote Desktop, and off when there is nobody to ask."""
    while widget is not None:
        pc = getattr(widget, "pc", None)
        if pc is not None:
            return bool(pc.animations())
        widget = widget.parentWidget()
    return False


def ink(value: str) -> QColor:
    """A color from the look, which is ``#RRGGBB`` or ``rgba(r, g, b, a)``."""
    if value.startswith("rgba"):
        r, g, b, a = (int(n) for n in re.findall(r"\d+", value))
        return QColor(r, g, b, a)
    return QColor(value)


def _painter(widget: QWidget) -> QPainter:
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    if not widget.isEnabled():
        painter.setOpacity(0.45)
    return painter


def _spring(owner: QObject, ms: int, moved: Callable[[float], None]) -> QVariantAnimation:
    """A short move that goes a little past its end and comes back (the
    design's curve, the same as the page list's marker)."""
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.3, 1.36), QPointF(0.44, 1), QPointF(1, 1))
    move = QVariantAnimation(owner)
    move.setDuration(ms)
    move.setEasingCurve(curve)
    move.valueChanged.connect(moved)
    return move


def _go(move: QVariantAnimation, widget: QWidget, start: float, end: float) -> bool:
    """Starts ``move``, unless animations are off or nobody can see it."""
    move.stop()
    if start == end or not widget.isVisible() or not animated(widget):
        return False
    move.setStartValue(float(start))
    move.setEndValue(float(end))
    move.start()
    return True


# ---------------------------------------------------------------- hover name
class Bubble(QLabel):
    """The small label that shows under a part: an icon button's name, an
    info dot's words, a player's name. Only one at a time, like Qt's own
    tooltip, but with our corners, edge and wait."""

    WIDE = 250
    one: Bubble | None = None

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWordWrap(True)
        self.setContentsMargins(10, 6, 10, 6)
        self.owner: QWidget | None = None
        self.look: dict[str, str] = {}
        self.wait = QTimer(self)
        self.wait.setSingleShot(True)
        self.wait.timeout.connect(self._show)

    @classmethod
    def over(cls, owner: QWidget, words: str, wait_ms: int = 350) -> None:
        """Shows ``words`` under ``owner`` after a short wait."""
        cls.away()
        bubble = cls.one = Bubble()
        bubble.owner = owner
        owner.destroyed.connect(bubble.hide)  # a page that is rebuilt takes its parts with it
        bubble.setText(words)
        bubble.wait.start(wait_ms)

    @classmethod
    def away(cls, owner: QWidget | None = None) -> None:
        """Takes the bubble away (only ``owner``'s, when one is given)."""
        bubble = cls.one
        if bubble is None or not isValid(bubble):
            cls.one = None
        elif owner is None or bubble.owner is owner:
            cls.one = None
            bubble.hide()
            bubble.deleteLater()

    @classmethod
    def words(cls) -> str:
        """What the bubble says now, or nothing while it is hidden."""
        bubble = cls.one
        return bubble.text() if bubble and isValid(bubble) and bubble.isVisible() else ""

    def _show(self) -> None:
        owner = self.owner
        if owner is None or not isValid(owner) or not owner.isVisible():
            return
        self.look = look_of(owner)
        font = QFont(owner.font())  # so it grows with Windows' text size
        font.setWeight(QFont.Weight.Normal)
        font.setPointSizeF(font.pointSizeF() * 0.95)
        self.setFont(font)
        self.setStyleSheet(f"color: {self.look['text-primary']};")
        wide = min(self.WIDE, QFontMetrics(font).horizontalAdvance(self.text()) + 24)
        self.setFixedSize(wide, self.heightForWidth(wide))
        under = owner.mapToGlobal(QPoint(owner.width() // 2, owner.height() + 8))
        x, y = under.x() - wide // 2, under.y()
        screen = owner.screen().availableGeometry()
        if y + self.height() > screen.bottom():  # no room under it: above
            y = owner.mapToGlobal(QPoint(0, 0)).y() - 8 - self.height()
        x = max(screen.left() + 4, min(x, screen.right() - wide - 4))
        self.move(x, y)
        self.show()

    def paintEvent(self, event) -> None:
        painter = _painter(self)
        painter.setBrush(ink(self.look["surface-float"]))
        painter.setPen(QPen(ink(self.look["border-strong"]), EDGE))
        half = EDGE / 2
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(half, half, -half, -half), 9, 9)
        painter.end()
        super().paintEvent(event)


class _Hover(QObject):
    """Shows a part's bubble while the mouse is on it, or while it has the
    keyboard after Tab."""

    KEYS = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)

    def eventFilter(self, part: QObject, event: QEvent) -> bool:
        if not isinstance(part, QWidget):
            return False
        kind = event.type()
        words = part.property("hoverName")
        if kind == QEvent.Type.Enter and words:
            Bubble.over(part, words, part.property("hoverWait"))
        elif kind == QEvent.Type.FocusIn and words and event.reason() in self.KEYS:  # type: ignore[attr-defined]
            Bubble.over(part, words, part.property("hoverWait"))
        elif kind in (
            QEvent.Type.Leave,
            QEvent.Type.FocusOut,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Hide,
        ):
            Bubble.away(part)
        return False


_hover = _Hover()


def hover_name(part: QWidget, words: str, wait_ms: int = 350) -> None:
    """Gives ``part`` a bubble with ``words`` on hover and on Tab."""
    if part.property("hoverName") is None:
        part.installEventFilter(_hover)
    part.setProperty("hoverName", words)
    part.setProperty("hoverWait", wait_ms)


# ---------------------------------------------------------------- raised box
class Box(QFrame):
    """A raised box: paper with an edge in the server's color and a soft
    shadow under it. Only for a thing you act on as one piece (a server, a
    mod, a setting with its control). What goes in it goes in ``column``."""

    SHADOW = 10  # room under the box for its shadow
    ROUND = 12

    def __init__(self) -> None:
        super().__init__()
        self.setContentsMargins(3 + 16, 1 + 12, 3 + 16, self.SHADOW + 14)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(8)

    def paper(self) -> QRectF:
        return QRectF(self.rect()).adjusted(3, 1, -3, -self.SHADOW)

    def paintEvent(self, event) -> None:
        look = look_of(self)
        painter = _painter(self)
        paper = self.paper()
        for step in range(self.SHADOW):  # darker toward the box, and narrower than it
            out = self.SHADOW - step
            painter.setBrush(QColor(0, 0, 0, 4))
            painter.drawRoundedRect(
                paper.adjusted(out, self.ROUND, -out, out - 1), self.ROUND, self.ROUND
            )
        painter.setBrush(ink(look["paper"]))
        painter.setPen(QPen(ink(look["border-strong"]), EDGE))
        painter.drawRoundedRect(paper, self.ROUND, self.ROUND)


# -------------------------------------------------------------- icon button
class IconButton(QToolButton):
    """A square button with only an icon. Its name shows on hover and is
    what Narrator reads. ``kind``: "" (paper), "accent" (the one filled
    button) or "danger" (quiet until the mouse is on it, then red)."""

    def __init__(self, icon: str, name: str, kind: str = "") -> None:
        super().__init__()
        self.icon_name = icon
        self.setObjectName("tool")
        self.setProperty("kind", kind)
        self.setAutoRaise(kind == "danger")  # so Qt swaps the icon's color on hover
        self.setAccessibleName(name)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        hover_name(self, name)

    def event(self, event: QEvent) -> bool:
        # The icon is a picture in the look's colors: draw it again when
        # the button first shows and each time the window's look changes.
        if event.type() in (QEvent.Type.Polish, QEvent.Type.StyleChange):
            self.tint(look_of(self))
        return super().event(event)

    def tint(self, look: dict[str, str]) -> None:
        kind = self.property("kind")
        if kind == "accent":
            self.setIcon(icons.icon(self.icon_name, look["accent-text"]))
        elif kind == "danger":
            self.setIcon(
                icons.icon(self.icon_name, look["text-secondary"], hover=look["on-danger"])
            )
        else:
            self.setIcon(icons.icon(self.icon_name, look["text-primary"]))


# ------------------------------------------------------------------ info dot
class InfoDot(QAbstractButton):
    """A small (i) next to a heading or a label. The sentence that explains
    it shows on hover, on Tab, on a click or Space, and to Narrator."""

    def __init__(self, words: str) -> None:
        super().__init__()
        self.setAccessibleName(text("help.what_is_this"))
        self.setAccessibleDescription(words)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)  # repaint when the mouse comes and goes
        hover_name(self, words, 80)
        self.clicked.connect(lambda: Bubble.over(self, words, 0))

    def sizeHint(self) -> QSize:
        side = max(16, round(self.fontMetrics().height() * 0.95)) + 2 * RING
        return QSize(side, side)

    def paintEvent(self, event) -> None:
        look = look_of(self)
        lit = self.underMouse() or self.hasFocus()
        color = ink(look["accent-ink" if lit else "text-secondary"])
        painter = _painter(self)
        circle = QRectF(self.rect()).adjusted(RING + 1, RING + 1, -RING - 1, -RING - 1)
        painter.setPen(QPen(color, EDGE))
        painter.drawEllipse(circle)
        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(max(10, round(circle.height() * 0.68)))
        painter.setFont(font)
        painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, "i")
        if self.hasFocus():
            painter.setPen(QPen(ink(look["accent-ring"]), RING))
            painter.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))


# -------------------------------------------------------------------- switch
class Switch(QAbstractButton):
    """On or off, and it applies at once (no Save button). It is a
    checkable button, so ``toggled`` and ``setChecked`` work like a tick
    box's, and Space flips it."""

    TRACK = QSize(44, 26)
    KNOB = 20
    SLIDE_MS = 380

    def __init__(self, name: str) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setAccessibleName(name)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.at = 0.0  # where the knob is: 0 off, 1 on
        self.slide = _spring(self, self.SLIDE_MS, self._slide_to)
        self.toggled.connect(self._flipped)

    def sizeHint(self) -> QSize:
        return self.TRACK + QSize(2 * RING + 2, 2 * RING + 2)

    def _flipped(self, on: bool) -> None:
        if not _go(self.slide, self, self.at, float(on)):
            self._slide_to(float(on))

    def _slide_to(self, at: float) -> None:
        self.at = at
        self.update()

    def paintEvent(self, event) -> None:
        look = look_of(self)
        painter = _painter(self)
        track = QRectF(0, 0, self.TRACK.width(), self.TRACK.height())
        track.moveCenter(QRectF(self.rect()).center())
        half = track.height() / 2
        # The edge stays when it is on: a yellow track alone is hard to see on white.
        painter.setBrush(ink(look["accent" if self.isChecked() else "surface-sunken"]))
        painter.setPen(QPen(ink(look["border-strong"]), EDGE))
        painter.drawRoundedRect(track, half, half)
        gap = (track.height() - self.KNOB) / 2
        travel = track.width() - self.KNOB - 2 * gap
        # The spring goes a little past the end; the knob stays on the track.
        x = track.left() + gap + travel * min(1.08, max(-0.08, self.at))
        _knob(painter, look, QRectF(x, track.top() + gap, self.KNOB, self.KNOB))
        if self.hasFocus():
            _focus_ring(painter, look, track.adjusted(-RING, -RING, RING, RING))


def _knob(painter: QPainter, look: dict[str, str], circle: QRectF) -> None:
    """The white handle of a switch or a slider. Its edge keeps it clear
    of a pale track (a yellow server's)."""
    painter.setBrush(QColor("#FFFFFF"))
    painter.setPen(QPen(ink(look["border-strong"]), EDGE))
    painter.drawEllipse(circle)


def _focus_ring(painter: QPainter, look: dict[str, str], around: QRectF) -> None:
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(ink(look["accent-ring"]), RING))
    half = around.height() / 2
    painter.drawRoundedRect(around.adjusted(1, 1, -1, -1), half, half)


# --------------------------------------------------------- segmented control
class Segmented(QWidget):
    """A few choices in one row, one of them picked, with a raised thumb
    that slides to the picked one. ``options`` are (key, words) pairs;
    ``pick`` gets the key a person clicked."""

    SLIDE_MS = 420

    def __init__(
        self, options: Sequence[tuple[str, str]], chosen: str, pick: Callable[[str], None]
    ) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(0)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for key, words in options:
            button = QPushButton(words)
            button.setObjectName("seg")
            button.setCheckable(True)
            button.setChecked(key == chosen)
            button.clicked.connect(lambda _=False, k=key: pick(k))
            self.group.addButton(button)
            self.buttons[key] = button
            row.addWidget(button, 1)
        self.at: float | None = None  # the thumb's left side while it slides
        self.slide = _spring(self, self.SLIDE_MS, self._slide_to)
        self.slide.finished.connect(lambda: self._slide_to(None))
        self.group.buttonToggled.connect(self._toggled)

    def sizeHint(self) -> QSize:
        hints = [b.sizeHint() for b in self.buttons.values() if not b.isHidden()]
        wide = max((h.width() for h in hints), default=0)
        tall = max((h.height() for h in hints), default=0)
        return QSize(len(hints) * wide + 4, tall + 4)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def key(self) -> str | None:
        return next((k for k, b in self.buttons.items() if b.isChecked()), None)

    def choose(self, key: str) -> None:
        """Picks ``key`` from code: the thumb moves, ``pick`` is not called."""
        self.buttons[key].setChecked(True)

    def _toggled(self, button: QAbstractButton, on: bool) -> None:
        if not on:
            return
        start = self._thumb().left() if self.at is None else self.at
        if not _go(self.slide, self, start, button.x()):
            self._slide_to(None)

    def _slide_to(self, x: float | None) -> None:
        self.at = x
        self.update()

    def _thumb(self) -> QRectF:
        button = self.group.checkedButton()
        return QRectF(button.geometry()) if button else QRectF()

    def paintEvent(self, event) -> None:
        look = look_of(self)
        painter = _painter(self)
        half = EDGE / 2
        painter.setBrush(ink(look["surface-sunken"]))
        painter.setPen(QPen(ink(look["border-strong"]), EDGE))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(half, half, -half, -half), 9, 9)
        thumb = self._thumb()
        if thumb.isEmpty():
            return
        if self.at is not None:
            thumb.moveLeft(self.at)
        thumb.adjust(1, 1, -1, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))  # a thin shadow under the thumb
        painter.drawRoundedRect(thumb.translated(0, 1), 7, 7)
        painter.setBrush(ink(look["surface-float"]))
        painter.drawRoundedRect(thumb, 7, 7)


# -------------------------------------------------------------------- slider
class SnapSlider(QSlider):
    """A slider with a thick track. While the mouse drags it the handle
    follows the mouse freely and the value snaps to the nearest step; let
    go, and the handle glides to that step. It is still a QSlider (range,
    value, ``valueChanged``, arrow keys, Narrator), only the drawing and
    the dragging are ours. Turn on tick marks with ``setTickInterval``."""

    THICK = 26
    GLIDE_MS = 380

    def __init__(self, name: str) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.setAccessibleName(name)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.at = 0.0  # where the handle is, 0 to 1 along the track
        self.dragging = False
        self.glide = _spring(self, self.GLIDE_MS, self._glide_to)

    def sizeHint(self) -> QSize:
        ticks = 8 if self.tickInterval() > 0 else 0
        return QSize(220, self.THICK + 2 * RING + ticks)

    def minimumSizeHint(self) -> QSize:
        return QSize(96, self.sizeHint().height())

    def _track(self) -> QRectF:
        return QRectF(RING, RING, self.width() - 2 * RING, self.THICK)

    def _share(self, value: int) -> float:
        span = self.maximum() - self.minimum()
        return (value - self.minimum()) / span if span else 0.0

    def handle_x(self) -> float:
        """The handle's middle, in the slider's own pixels."""
        track = self._track()
        return track.left() + self.THICK / 2 + (track.width() - self.THICK) * self.at

    def _glide_to(self, at: float) -> None:
        self.at = at
        self.update()

    def sliderChange(self, change: QAbstractSlider.SliderChange) -> None:
        super().sliderChange(change)
        if self.dragging:
            return
        target = self._share(self.value())
        moved = change == QAbstractSlider.SliderChange.SliderValueChange
        if not (moved and _go(self.glide, self, self.at, target)):
            self.glide.stop()
            self._glide_to(target)

    def _follow(self, x: float) -> None:
        track = self._track()
        room = track.width() - self.THICK
        share = (x - track.left() - self.THICK / 2) / room if room > 0 else 0.0
        self.at = min(1.0, max(0.0, share))
        span = self.maximum() - self.minimum()
        self.setValue(self.minimum() + round(self.at * span))
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.glide.stop()
        self.dragging = True
        self.setSliderDown(True)
        self._follow(event.position().x())

    def mouseMoveEvent(self, event) -> None:
        if self.dragging:
            self._follow(event.position().x())

    def mouseReleaseEvent(self, event) -> None:
        if not self.dragging:
            return super().mouseReleaseEvent(event)
        self.dragging = False
        self.setSliderDown(False)
        target = self._share(self.value())
        if not _go(self.glide, self, self.at, target):
            self._glide_to(target)

    def paintEvent(self, event) -> None:
        look = look_of(self)
        painter = _painter(self)
        track = self._track()
        half = self.THICK / 2
        edge = QPen(ink(look["border-strong"]), EDGE)
        painter.setBrush(ink(look["surface-sunken"]))
        painter.drawRoundedRect(track, half, half)
        # The spring goes a little past the step; the handle stays on the track.
        at = min(1.0, max(0.0, self.at))
        x = track.left() + (track.width() - self.THICK) * at
        filled = QRectF(track.left(), track.top(), x - track.left() + self.THICK, self.THICK)
        painter.setBrush(ink(look["accent"]))
        painter.drawRoundedRect(filled, half, half)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(edge)  # over the fill: a yellow fill alone is hard to see on white
        painter.drawRoundedRect(track, half, half)
        _knob(painter, look, QRectF(x, track.top(), self.THICK, self.THICK).adjusted(1, 1, -1, -1))
        if self.tickInterval() > 0:
            painter.setPen(QPen(ink(look["border-strong"]), 1))
            for value in range(self.minimum(), self.maximum() + 1, self.tickInterval()):
                tick = track.left() + half + (track.width() - self.THICK) * self._share(value)
                painter.drawLine(
                    QPointF(tick, track.bottom() + 4), QPointF(tick, track.bottom() + 9)
                )
        if self.hasFocus():
            _focus_ring(painter, look, track.adjusted(-RING, -RING, RING, RING))


# ---------------------------------------------------------------- big number
class BigNumber(QLineEdit):
    """A big number that is also its own field: click it, type, Enter.
    ``entered`` gives the number that was typed. The field goes back to
    the last number it was told to show (``show_value``) until the page
    shows the new one, so it never shows a number that was not saved."""

    entered = Signal(float)

    def __init__(self, name: str) -> None:
        super().__init__()
        self.setObjectName("bigNumber")
        self.setAccessibleName(name)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.shown = ""
        self.textChanged.connect(self._fit)
        self.editingFinished.connect(self._typed)

    def show_value(self, words: str) -> None:
        self.shown = words
        self.setText(words)

    def _fit(self) -> None:
        """As wide as its digits, so the unit sits right after it."""
        wide = self.fontMetrics().horizontalAdvance(self.text() or "0")
        self.setFixedWidth(wide + 22)

    def _typed(self) -> None:
        typed = self.text().strip()
        self.setText(self.shown)
        if typed == self.shown:
            return
        value, ok = QLocale().toDouble(typed)
        if ok and math.isfinite(value):
            self.entered.emit(value)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.setText(self.shown)
            self.selectAll()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        QTimer.singleShot(0, self.selectAll)  # typing replaces the number

    def event(self, event: QEvent) -> bool:
        done = super().event(event)
        if event.type() in (QEvent.Type.Polish, QEvent.Type.StyleChange):
            self._fit()  # the big font is known now, or Windows' text size changed
        return done


# -------------------------------------------------------------- search field
class Search(QLineEdit):
    """A search field: a magnifying glass in it, no Search button. The
    page listens to ``textChanged`` or ``returnPressed``."""

    GLASS = 16

    def __init__(self, placeholder: str) -> None:
        super().__init__()
        self.setObjectName("search")
        self.setPlaceholderText(placeholder)
        self.setAccessibleName(placeholder)
        self.setTextMargins(self.GLASS + 8, 0, 0, 0)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        glass = icons.icon("search", look_of(self)["text-secondary"], self.GLASS)
        top = (self.height() - self.GLASS) // 2
        painter = QPainter(self)
        glass.paint(painter, QRect(10, top, self.GLASS, self.GLASS))


# --------------------------------------------------------------- status mark
class Status(QLabel):
    """One steady dot and a word, in the state's color: "on" green, "wait"
    amber, "bad" red, "off" grey. Never a pill."""

    COLORS = {"on": "success", "wait": "warning", "bad": "danger", "off": "text-secondary"}
    DOT, GAP = 10, 6

    def __init__(self, state: str = "off", words: str = "") -> None:
        super().__init__()
        self.state = state
        self.say(state, words)

    def say(self, state: str, words: str) -> None:
        self.state = state
        self.setText(words)
        self.updateGeometry()
        self.update()

    def _font(self) -> QFont:
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self._font())
        wide = self.DOT + self.GAP + metrics.horizontalAdvance(self.text()) + 2
        return QSize(wide, max(self.DOT, metrics.height()))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        look = look_of(self)
        painter = _painter(self)
        middle = self.height() / 2
        dot = "neutral" if self.state == "off" else self.COLORS[self.state]
        painter.setBrush(ink(look[dot]))
        painter.drawEllipse(QPointF(self.DOT / 2, middle), self.DOT / 2, self.DOT / 2)
        painter.setPen(ink(look[self.COLORS[self.state]]))
        painter.setFont(self._font())
        words = QRectF(self.rect()).adjusted(self.DOT + self.GAP, 0, 0, 0)
        painter.drawText(
            words, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text()
        )


# --------------------------------------------------------------- player face
class Face(QWidget):
    """A player's head. A plain grey head for everyone until the app can
    get real skins; the player's name shows on hover."""

    def __init__(self, name: str, side: int = 22) -> None:
        super().__init__()
        self.setFixedSize(side, side)
        self.setAccessibleName(name)
        hover_name(self, name)

    def paintEvent(self, event) -> None:
        look = look_of(self)
        painter = _painter(self)
        half = EDGE / 2
        head = QRectF(self.rect()).adjusted(half, half, -half, -half)
        painter.setBrush(ink(look["neutral"]))
        painter.setPen(QPen(ink(look["border-strong"]), EDGE))
        painter.drawRoundedRect(head, 6, 6)
        # Two eyes, on the 8 by 8 grid a Minecraft face is drawn on.
        cell = self.width() / 8
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink(look["paper"]))
        for column in (1.5, 5):
            painter.drawRect(QRectF(column * cell, 4 * cell, 1.5 * cell, cell))
